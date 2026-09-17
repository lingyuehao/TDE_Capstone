"""
Step 0 (placeholder) of the pipeline: pull new charge data from an
external API once a day and land it in s3://.../raw/, in the same shape
the rest of the pipeline (clean_charge_raw.py onward) already expects.

There is no live API to call yet -- this is a placeholder for that
future source. The shape below (Job parameters, auth via Secrets
Manager, write to RAW_CHARGE_S3) is what the real implementation should
look like once there's an actual endpoint; swap the body of
`fetch_from_api()` for the real call when that's available. Until then,
running this job raises NotImplementedError on purpose rather than
silently doing nothing.

Intended to run once a day ahead of the rest of the pipeline (via a
daily EventBridge Scheduler rule -- see the README's "Live data
ingestion" section, which documents the intended wiring; nothing is
deployed yet). Once this job writes a fresh raw file, no separate
"update the dashboard" step is needed -- the existing pipeline
(clean_charge_raw.py -> ... -> dashboard.py) already regenerates
dashboard/summary.json from whatever's in raw/ each time it runs.

Output must match the raw export schema clean_charge_raw.py expects
(see its COLUMNS list): tde_tracking_detail_id, tracking_detail_name,
currency, created_date, created_by_full_name, last_modified_date,
last_modified_by_full_name, last_activity_date, ar_name,
charge_description, charge_type, charge_value, data_import_name,
migration_info.

Job parameters (set in the Glue job's "Job parameters" field):
  --API_ENDPOINT      Placeholder -- the real API's URL once one exists.
  --API_SECRET_NAME   Placeholder -- Secrets Manager secret holding the
                       API key/token once one exists.
  --RAW_CHARGE_S3      e.g. s3://capstone-tde-demo/raw/trackingdetail.csv
"""

import sys

import boto3
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(sys.argv, ["API_ENDPOINT", "API_SECRET_NAME", "RAW_CHARGE_S3"])
API_ENDPOINT = args["API_ENDPOINT"]
API_SECRET_NAME = args["API_SECRET_NAME"]
RAW_CHARGE_S3 = args["RAW_CHARGE_S3"]

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")


def parse_s3_path(s3_path):
    assert s3_path.startswith("s3://"), f"Not an s3:// path: {s3_path}"
    bucket, key = s3_path[5:].split("/", 1)
    return bucket, key


def get_api_key():
    """Placeholder -- assumes the real API's key/token will be stored in
    Secrets Manager under API_SECRET_NAME. Replace if the real API uses a
    different auth scheme."""
    resp = secrets.get_secret_value(SecretId=API_SECRET_NAME)
    return resp["SecretString"]


def fetch_from_api(endpoint, api_key):
    """Placeholder -- replace with the real API call once one exists.
    Must return raw CSV bytes matching the schema documented in this
    file's module docstring."""
    raise NotImplementedError(
        "ingest_api.py is a placeholder -- no live API is configured yet. "
        "Implement fetch_from_api() with the real endpoint/auth, then "
        "flip RUN_INGEST_STEP in orchestrate.py before wiring this job "
        "into the daily schedule."
    )


def main():
    api_key = get_api_key()
    payload = fetch_from_api(API_ENDPOINT, api_key)

    bucket, key = parse_s3_path(RAW_CHARGE_S3)
    s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="text/csv")
    print(f"Wrote fetched data to {RAW_CHARGE_S3}")


if __name__ == "__main__":
    main()
