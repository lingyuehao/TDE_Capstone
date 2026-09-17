"""
Step 4 of the pipeline: rebuild charge_categorized.

Glue Python Shell job.

charge_categorized is an EXTERNAL Athena table over Parquet files. It
holds charge_raw_clean's columns joined to charge_mapping's category
labels. There is no way for it to "auto-update" when charge_mapping
changes -- it has to be rebuilt (drop + CTAS) every time the taxonomy is
re-run. This job does that rebuild via the Athena API instead of by hand
in the console, so a taxonomy change never requires a manual SQL step.

Unmatched rows (a (charge_type, charge_description) pair in
charge_raw_clean with no corresponding row in charge_mapping) default to
the same catch-all label taxonomy.py uses ("Other / Uncategorized" /
"Unclassified") via COALESCE, rather than being left NULL -- an
unlabeled NULL bucket looks like a bug in any downstream chart, while a
labeled catch-all bucket is exactly what it is: charge labels the
taxonomy doesn't cover yet.

charge_value is TRY_CAST to double rather than CAST, because a small
number of rows have non-numeric junk in that column (a data-quality issue
in the source export, not something this job can fix) -- TRY_CAST nulls
those specific values out instead of failing the whole rebuild.

Run this AFTER charge_type.py (which refreshes charge_mapping) and
BEFORE dashboard.py (which reads charge_categorized).

Job parameters (set in the Glue job's "Job parameters" field):
  --ATHENA_DATABASE       e.g. tde_demo
  --ATHENA_OUTPUT_S3      e.g. s3://capstone-tde-demo/athena-results/
  --CATEGORIZED_S3_PATH   e.g. s3://capstone-tde-demo/categorized/
  --RAW_TABLE             e.g. charge_raw_clean
  --MAPPING_TABLE         e.g. charge_mapping
  --OUTPUT_TABLE          e.g. charge_categorized
"""

import sys
import time

import boto3
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(
    sys.argv,
    [
        "ATHENA_DATABASE",
        "ATHENA_OUTPUT_S3",
        "CATEGORIZED_S3_PATH",
        "RAW_TABLE",
        "MAPPING_TABLE",
        "OUTPUT_TABLE",
    ],
)
ATHENA_DATABASE = args["ATHENA_DATABASE"]
ATHENA_OUTPUT_S3 = args["ATHENA_OUTPUT_S3"]
CATEGORIZED_S3_PATH = args["CATEGORIZED_S3_PATH"].rstrip("/") + "/"
RAW_TABLE = args["RAW_TABLE"]
MAPPING_TABLE = args["MAPPING_TABLE"]
OUTPUT_TABLE = args["OUTPUT_TABLE"]

athena = boto3.client("athena")
s3 = boto3.client("s3")

DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {OUTPUT_TABLE}"

CTAS_SQL = f"""
CREATE TABLE {OUTPUT_TABLE}
WITH (
  format = 'PARQUET',
  parquet_compression = 'GZIP',
  external_location = '{CATEGORIZED_S3_PATH}'
) AS
SELECT
  r.tde_tracking_detail_id,
  r.tracking_detail_name,
  r.currency,
  r.created_date,
  r.ar_name,
  r.charge_type,
  r.charge_description,
  TRY_CAST(r.charge_value AS double) AS charge_value,
  r.data_import_name,
  COALESCE(m.major_category, 'Other / Uncategorized') AS major_category,
  COALESCE(m.subcategory, 'Unclassified') AS subcategory,
  COALESCE(m.method, 'unmapped') AS method
FROM {RAW_TABLE} r
LEFT JOIN {MAPPING_TABLE} m
  ON r.charge_type = m.charge_type
  AND r.charge_description = m.charge_description
"""


def parse_s3_path(s3_path):
    assert s3_path.startswith("s3://"), f"Not an s3:// path: {s3_path}"
    bucket, key = s3_path[5:].split("/", 1)
    return bucket, key


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


def clear_s3_prefix(s3_path):
    """CTAS with external_location requires the target prefix to be empty --
    DROP TABLE only removes the Glue Data Catalog entry, not the
    underlying S3 objects, so a previous build's Parquet files would
    otherwise block this rebuild."""
    bucket, prefix = parse_s3_path(s3_path)
    paginator = s3.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


print(f"Dropping old {OUTPUT_TABLE} table (Glue Data Catalog entry only)...")
run_query(DROP_TABLE_SQL, ATHENA_DATABASE, ATHENA_OUTPUT_S3)

print(f"Clearing old Parquet files at {CATEGORIZED_S3_PATH} ...")
deleted_count = clear_s3_prefix(CATEGORIZED_S3_PATH)
print(f"Deleted {deleted_count} object(s)")

print(f"Rebuilding {OUTPUT_TABLE} via CTAS ({RAW_TABLE} JOIN {MAPPING_TABLE})...")
run_query(CTAS_SQL, ATHENA_DATABASE, ATHENA_OUTPUT_S3)

print(f"{OUTPUT_TABLE} rebuilt successfully.")
