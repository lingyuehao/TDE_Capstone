"""
Step 2 of the pipeline: clean the raw Data Import export and build the
carrier/client lookup table.

Glue Python Shell job.

Why this exists: the raw "Data Import" export contains large JSON/URL
fields (FieldMapping JSON, Document URL, etc.) with embedded newlines
inside quoted values -- the same multi-line-quoting problem
clean_charge_raw.py fixes for the charge export, but here the fix also
does a projection + dedup, since the dashboard only ever needs
carrier/client per import batch, not the full raw export. Athena's
OpenCSVSerde reads a file line-by-line and does not respect quotes
spanning multiple physical lines, so pointing an external table straight
at that raw file corrupts row boundaries -- one logical record gets split
into many garbage "rows." Python's csv module does not have that
limitation: it correctly reassembles a quoted field that spans multiple
physical lines back into a single record. So this job does the parsing in
Python, keeps only the three columns the dashboard actually needs, and
deduplicates on Data Import Name (each import batch is one row here, even
though the raw export has one row per invoice within that batch).

Downstream: sql/04_data_import_lookup.sql creates the Athena table over
this job's output; dashboard.py joins charge_categorized to it.

Job parameters (set in the Glue job's "Job parameters" field):
  --RAW_DATA_IMPORT_S3   e.g. s3://capstone-tde-demo/raw/Data_Import_Table.csv
  --LOOKUP_OUTPUT_S3     e.g. s3://capstone-tde-demo/curated/data_import_lookup/data_import_lookup.csv
"""

import csv
import io
import sys

import boto3
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(sys.argv, ["RAW_DATA_IMPORT_S3", "LOOKUP_OUTPUT_S3"])
RAW_DATA_IMPORT_S3 = args["RAW_DATA_IMPORT_S3"]
LOOKUP_OUTPUT_S3 = args["LOOKUP_OUTPUT_S3"]

csv.field_size_limit(sys.maxsize)
s3 = boto3.client("s3")


def parse_s3_path(s3_path):
    bucket, key = s3_path[5:].split("/", 1)
    return bucket, key


def stream_csv_rows(s3_path):
    """Stream a CSV directly from S3 and yield parsed rows. Wrapping the
    streaming body in TextIOWrapper lets csv.reader pull additional
    physical lines on its own whenever it's still inside an open quote --
    that's what correctly reassembles the multi-line JSON fields."""
    bucket, key = parse_s3_path(s3_path)
    obj = s3.get_object(Bucket=bucket, Key=key)
    text_stream = io.TextIOWrapper(obj["Body"], encoding="utf-8", errors="replace")
    return csv.reader(text_stream)


print(f"Reading raw export from {RAW_DATA_IMPORT_S3} ...")
reader = stream_csv_rows(RAW_DATA_IMPORT_S3)
header = next(reader)

idx_name = header.index("Data Import Name")
idx_carrier = header.index("Carrier Name")
idx_client = header.index("Client Name")

seen = set()
lookup_rows = []
total_rows = 0
skipped_short_rows = 0

for row in reader:
    total_rows += 1
    if idx_client >= len(row):
        skipped_short_rows += 1
        continue

    name = row[idx_name]
    carrier = row[idx_carrier].strip()
    client = row[idx_client].strip()

    if name in seen:
        continue
    seen.add(name)
    lookup_rows.append([name, carrier, client])

print(f"Read {total_rows} source rows, skipped {skipped_short_rows} malformed rows")
print(f"Deduplicated down to {len(lookup_rows)} unique import batches")

# Write the clean lookup CSV in memory, then upload it. Small enough
# (one row per import batch, not per invoice) that this is fine as a
# single put_object rather than a multipart upload.
buf = io.StringIO()
writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
writer.writerow(["data_import_name", "carrier_name", "client_name"])
writer.writerows(lookup_rows)

bucket, key = parse_s3_path(LOOKUP_OUTPUT_S3)
s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode("utf-8"), ContentType="text/csv")

print(f"Wrote clean lookup table to {LOOKUP_OUTPUT_S3}")
