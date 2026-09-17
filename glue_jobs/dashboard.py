"""
Step 5 of the pipeline: generate the dashboard summary.

Glue Python Shell job.

Runs a major-category + subcategory breakdown query against the
charge_categorized Athena table, waits for it to finish, and writes the
result as a small JSON file to S3. The live dashboard page (index.html)
fetches this file directly, so re-running this job is what makes the
dashboard "refresh." This job only re-aggregates charge_categorized -- it
does NOT rebuild it. Run build_charge_categorized.py first if
charge_mapping has changed.

Job parameters (set in the Glue job's "Job parameters" field):
  --ATHENA_DATABASE     e.g. tde_demo
  --ATHENA_OUTPUT_S3    e.g. s3://capstone-tde-demo/athena-results/
  --SUMMARY_S3_PATH     e.g. s3://capstone-tde-demo/dashboard/summary.json
"""

import json
import sys
import time
from collections import OrderedDict

import boto3
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(
    sys.argv, ["ATHENA_DATABASE", "ATHENA_OUTPUT_S3", "SUMMARY_S3_PATH"]
)
ATHENA_DATABASE = args["ATHENA_DATABASE"]
ATHENA_OUTPUT_S3 = args["ATHENA_OUTPUT_S3"]
SUMMARY_S3_PATH = args["SUMMARY_S3_PATH"]

# COALESCE(SUM(...), 0) below: charge_categorized's charge_value column is
# TRY_CAST from a raw varchar column, which nulls out any row where the raw
# value wasn't actually numeric. If every row in a GROUP BY bucket happens
# to be one of those null rows, SUM() returns NULL for that bucket instead
# of 0, and float(None) later in this script would throw -- COALESCE
# guarantees a numeric 0 in that case instead.
QUERY = """
SELECT
  major_category,
  subcategory,
  COUNT(*) AS num_rows,
  COALESCE(SUM(charge_value), 0) AS total_value
FROM charge_categorized
GROUP BY major_category, subcategory
ORDER BY major_category, total_value DESC
"""

# Company and carrier live in a separate table, not on charge_categorized
# itself: `ar_name` on the tracking-detail export is 100% blank, but every
# charge row carries `data_import_name`, which joins cleanly to the
# cleaned Data Import lookup (client_name + carrier_name are both
# perfectly consistent per data_import_name -- verified no batch mixes
# carriers or clients). data_import_lookup is already deduplicated to one
# row per import batch (see clean_data_import.py), so this join doesn't
# fan out and inflate row counts.
COMPANY_QUERY = """
SELECT
  COALESCE(NULLIF(TRIM(d.client_name), ''), 'Unspecified account') AS company,
  c.major_category,
  COUNT(*) AS num_rows,
  COALESCE(SUM(c.charge_value), 0) AS total_value
FROM charge_categorized c
LEFT JOIN data_import_lookup d
  ON c.data_import_name = d.data_import_name
GROUP BY COALESCE(NULLIF(TRIM(d.client_name), ''), 'Unspecified account'), c.major_category
ORDER BY company, total_value DESC
"""

CARRIER_QUERY = """
SELECT
  COALESCE(NULLIF(TRIM(d.carrier_name), ''), 'Unspecified carrier') AS carrier,
  c.major_category,
  COUNT(*) AS num_rows,
  COALESCE(SUM(c.charge_value), 0) AS total_value
FROM charge_categorized c
LEFT JOIN data_import_lookup d
  ON c.data_import_name = d.data_import_name
GROUP BY COALESCE(NULLIF(TRIM(d.carrier_name), ''), 'Unspecified carrier'), c.major_category
ORDER BY carrier, total_value DESC
"""

# Only ship the top N in the JSON so the dashboard payload (and the
# dropdown lists) stay manageable even with dozens/hundreds of distinct
# companies or carriers.
TOP_N_COMPANIES = 24
TOP_N_CARRIERS = 24

athena = boto3.client("athena")
s3 = boto3.client("s3")


def run_query(sql, database, output_location):
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location},
    )
    query_id = resp["QueryExecutionId"]

    while True:
        status = athena.get_query_execution(QueryExecutionId=query_id)
        state = status["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)

    if state != "SUCCEEDED":
        reason = status["QueryExecution"]["Status"].get("StateChangeReason", "unknown error")
        raise RuntimeError(f"Athena query {state}: {reason}")

    return query_id


def fetch_results(query_id):
    rows = []
    paginator = athena.get_paginator("get_query_results")
    header_skipped = False
    for page in paginator.paginate(QueryExecutionId=query_id):
        for row in page["ResultSet"]["Rows"]:
            values = [c.get("VarCharValue") for c in row["Data"]]
            if not header_skipped:
                header_skipped = True
                continue
            rows.append(values)
    return rows


def parse_s3_path(s3_path):
    bucket, key = s3_path[5:].split("/", 1)
    return bucket, key


print("Running major/subcategory summary query...")
query_id = run_query(QUERY, ATHENA_DATABASE, ATHENA_OUTPUT_S3)
rows = fetch_results(query_id)

print("Running per-company breakdown query...")
company_query_id = run_query(COMPANY_QUERY, ATHENA_DATABASE, ATHENA_OUTPUT_S3)
company_rows = fetch_results(company_query_id)

print("Running per-carrier breakdown query...")
carrier_query_id = run_query(CARRIER_QUERY, ATHENA_DATABASE, ATHENA_OUTPUT_S3)
carrier_rows = fetch_results(carrier_query_id)

# Group the (major, sub) rows into nested category objects, preserving
# first-seen order of major categories (Athena's ORDER BY major_category
# keeps each major category's rows contiguous).
majors = OrderedDict()
for major_name, sub_name, num_rows, total_value in rows:
    num_rows = int(num_rows)
    total_value = round(float(total_value), 2)

    if major_name not in majors:
        majors[major_name] = {"name": major_name, "row_count": 0, "total_value": 0.0, "subs": []}

    majors[major_name]["row_count"] += num_rows
    majors[major_name]["total_value"] = round(majors[major_name]["total_value"] + total_value, 2)
    majors[major_name]["subs"].append({
        "name": sub_name,
        "row_count": num_rows,
        "total_value": total_value,
    })

categories = list(majors.values())


def group_by_entity(entity_rows, top_n):
    """Group (entity_name, major, num_rows, total_value) rows into per-entity
    objects with a nested category breakdown, then split into the top N by
    charge magnitude and everything else."""
    entities_map = OrderedDict()
    for entity_name, major_name, num_rows, total_value in entity_rows:
        num_rows = int(num_rows)
        total_value = round(float(total_value), 2)

        if entity_name not in entities_map:
            entities_map[entity_name] = {
                "name": entity_name,
                "row_count": 0,
                "total_value": 0.0,
                "abs_total_value": 0.0,
                "categories": [],
            }

        e = entities_map[entity_name]
        e["row_count"] += num_rows
        e["total_value"] = round(e["total_value"] + total_value, 2)
        e["abs_total_value"] = round(e["abs_total_value"] + abs(total_value), 2)
        e["categories"].append({
            "name": major_name,
            "row_count": num_rows,
            "total_value": total_value,
        })

    all_entities = sorted(entities_map.values(), key=lambda e: e["abs_total_value"], reverse=True)
    top = all_entities[:top_n]
    rest = all_entities[top_n:]
    for e in top:
        del e["abs_total_value"]
    return all_entities, top, rest


all_companies, top_companies, rest_companies = group_by_entity(company_rows, TOP_N_COMPANIES)
all_carriers, top_carriers, rest_carriers = group_by_entity(carrier_rows, TOP_N_CARRIERS)

summary = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "total_transactions": sum(c["row_count"] for c in categories),
    "categories": categories,
    "companies": top_companies,
    "other_companies_count": len(rest_companies),
    "other_companies_row_count": sum(c["row_count"] for c in rest_companies),
    "other_companies_total_value": round(sum(c["total_value"] for c in rest_companies), 2),
    "carriers": top_carriers,
    "other_carriers_count": len(rest_carriers),
    "other_carriers_row_count": sum(c["row_count"] for c in rest_carriers),
    "other_carriers_total_value": round(sum(c["total_value"] for c in rest_carriers), 2),
}

bucket, key = parse_s3_path(SUMMARY_S3_PATH)
s3.put_object(
    Bucket=bucket,
    Key=key,
    Body=json.dumps(summary, indent=2),
    ContentType="application/json",
)
print(f"Wrote summary to {SUMMARY_S3_PATH}")
print(f"{len(categories)} major categories, {sum(len(c['subs']) for c in categories)} subcategories")
print(f"{len(all_companies)} companies found, shipping top {len(top_companies)} "
      f"({len(rest_companies)} rolled into 'other')")
print(f"{len(all_carriers)} carriers found, shipping top {len(top_carriers)} "
      f"({len(rest_carriers)} rolled into 'other')")
