"""
Step 1 of the pipeline: clean the raw charge export.

Glue Python Shell job.

Why this exists: the raw tracking-detail export sometimes has quoted
fields containing embedded newlines (e.g. a memo/description field with an
actual line break inside it). A plain line-based CSV reader -- including
Athena's OpenCSVSerde -- reads a file line-by-line and does not respect
quotes spanning multiple physical lines, so one logical record gets split
into multiple garbage rows and everything after the break shifts into the
wrong columns. Python's csv module does not have that limitation: it
correctly reassembles a quoted field that spans multiple physical lines
back into a single record.

This job re-parses the raw file with csv.reader and, for every field,
replaces any embedded \\n / \\r with a single space so every output row is
guaranteed to be exactly one physical line -- safe for OpenCSVSerde (or any
line-based reader) to parse afterward. Column values are otherwise left
untouched (no dedup, no filtering, no renaming) -- one row in, one row out.

Downstream: sql/02_charge_raw_clean.sql creates the Athena table over this
job's output.

Job parameters (set in the Glue job's "Job parameters" field):
  --RAW_CHARGE_S3     e.g. s3://capstone-tde-demo/raw/trackingdetail.csv
  --CLEAN_OUTPUT_S3   e.g. s3://capstone-tde-demo/curated/charge_raw_clean/charge_raw_clean.csv
"""

import csv
import io
import re
import sys

import boto3
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(sys.argv, ["RAW_CHARGE_S3", "CLEAN_OUTPUT_S3"])
RAW_CHARGE_S3 = args["RAW_CHARGE_S3"]
CLEAN_OUTPUT_S3 = args["CLEAN_OUTPUT_S3"]

csv.field_size_limit(sys.maxsize)
s3 = boto3.client("s3")

# Column order exactly as expected on the charge_raw_clean table's DDL
# (sql/02_charge_raw_clean.sql).
COLUMNS = [
    "tde_tracking_detail_id",
    "tracking_detail_name",
    "currency",
    "created_date",
    "created_by_full_name",
    "last_modified_date",
    "last_modified_by_full_name",
    "last_activity_date",
    "ar_name",
    "charge_description",
    "charge_type",
    "charge_value",
    "data_import_name",
    "migration_info",
]

NEWLINE_RE = re.compile(r"[\r\n]+")


def parse_s3_path(s3_path):
    assert s3_path.startswith("s3://"), f"Not an s3:// path: {s3_path}"
    bucket, key = s3_path[5:].split("/", 1)
    return bucket, key


def stream_csv_rows(s3_path):
    """Stream a CSV directly from S3 and yield parsed rows. Wrapping the
    streaming body in TextIOWrapper lets csv.reader pull additional
    physical lines on its own whenever it's still inside an open quote --
    that's what correctly reassembles the multi-line fields."""
    bucket, key = parse_s3_path(s3_path)
    obj = s3.get_object(Bucket=bucket, Key=key)
    text_stream = io.TextIOWrapper(obj["Body"], encoding="utf-8", errors="replace")
    return csv.reader(text_stream)


print(f"Reading raw export from {RAW_CHARGE_S3} ...")
reader = stream_csv_rows(RAW_CHARGE_S3)
header = next(reader)
print(f"Source header ({len(header)} columns): {header}")

total_rows = 0
short_rows = 0
long_rows = 0
newline_fields_fixed = 0

# Write output incrementally to a local buffer (this table is large --
# tens of millions of rows -- so build the CSV in one streaming pass
# rather than holding all rows in a Python list in memory).
buf = io.StringIO()
writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
writer.writerow(COLUMNS)

for row in reader:
    total_rows += 1

    if len(row) < len(COLUMNS):
        short_rows += 1
        # Pad missing trailing columns rather than dropping the row --
        # keeps row counts reconcilable against the raw export.
        row = row + [""] * (len(COLUMNS) - len(row))
    elif len(row) > len(COLUMNS):
        long_rows += 1
        # Extra columns beyond what's expected -- truncate rather than
        # guess which extra delimiter caused the overflow.
        row = row[: len(COLUMNS)]

    cleaned = []
    for field in row:
        if field and NEWLINE_RE.search(field):
            newline_fields_fixed += 1
            field = NEWLINE_RE.sub(" ", field).strip()
        cleaned.append(field)

    writer.writerow(cleaned)

    if total_rows % 1_000_000 == 0:
        print(f"...processed {total_rows:,} rows so far")

print(f"Processed {total_rows:,} total rows")
print(f"  {short_rows:,} rows had fewer fields than expected (padded)")
print(f"  {long_rows:,} rows had more fields than expected (truncated)")
print(f"  {newline_fields_fixed:,} individual field values had embedded newlines removed")

bucket, key = parse_s3_path(CLEAN_OUTPUT_S3)
s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode("utf-8"), ContentType="text/csv")
print(f"Wrote cleaned file to {CLEAN_OUTPUT_S3}")
