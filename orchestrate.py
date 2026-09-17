"""
Run the full charge-categorization pipeline end-to-end, entirely from
code -- no manual console/Athena steps.

This is a one-time/on-demand orchestrator, not a scheduled/recurring
system: it's meant for "I changed the taxonomy (or the raw data), now
propagate that all the way to the dashboard" -- run it, wait, done. For a
demo with no recurring new data, that's simpler and more transparent than
standing up a Glue Workflow or Step Functions state machine for something
that runs occasionally by hand anyway.

Sequence:
  0. (placeholder, off by default) Run ingest-api Glue job -> fresh raw
     file in S3, pulled from an external API. See RUN_INGEST_STEP below
     and glue_jobs/ingest_api.py's docstring -- there's no live API
     wired up yet, so this step is skipped unless explicitly enabled.
  1. Run clean-charge-raw Glue job          -> charge_raw_clean file in S3
  2. Run clean-data-import Glue job         -> data_import_lookup file in S3
  3. Ensure Athena tables exist (DDL)       -> charge_raw_clean, data_import_lookup, charge_mapping
  4. Run charge-type Glue job               -> charge_mapping file in S3 (table already exists from step 3)
  5. Run build-charge-categorized Glue job  -> drops + rebuilds charge_categorized table
  6. Run dashboard Glue job                 -> summary.json in S3 (what index.html reads)

Usage:
  python orchestrate.py

Requires AWS credentials configured (same profile/role that has access
to the Glue jobs, Athena, and S3 bucket below) and the Glue job names to
already exist in Glue (see README.md for the exact job parameters each
one needs -- this script only triggers runs, it doesn't create the jobs
themselves).
"""

import sys
import time

import boto3

# ---------------------------------------------------------------------------
# Configuration -- edit these to match your environment
# ---------------------------------------------------------------------------
AWS_REGION = "us-east-1"
ATHENA_DATABASE = "tde_demo"
ATHENA_OUTPUT_S3 = "s3://capstone-tde-demo/athena-results/"

# Flip to True once glue_jobs/ingest_api.py has a real API endpoint/secret
# wired up (it currently raises NotImplementedError on purpose). Until
# then, leave this False -- there's nothing live for it to pull from, and
# running it would just fail the whole pipeline before the real steps
# (which still work fine against whatever's already in raw/) get a chance
# to run.
RUN_INGEST_STEP = False

GLUE_JOB_NAMES = {
    "ingest_api": "ingest-api",
    "clean_charge_raw": "clean-charge-raw",
    "clean_data_import": "clean-data-import",
    "charge_type": "charge-type",
    "build_charge_categorized": "build-charge-categorized",
    "dashboard": "dashboard",
}

DDL_STATEMENTS = [
    ("charge_raw_clean", "sql/02_charge_raw_clean.sql"),
    ("data_import_lookup", "sql/04_data_import_lookup.sql"),
    ("charge_mapping", "sql/05_charge_mapping.sql"),
]

glue = boto3.client("glue", region_name=AWS_REGION)
athena = boto3.client("athena", region_name=AWS_REGION)


def run_glue_job(job_name, poll_seconds=10):
    print(f"\n--- Starting Glue job: {job_name} ---")
    resp = glue.start_job_run(JobName=job_name)
    run_id = resp["JobRunId"]

    while True:
        status = glue.get_job_run(JobName=job_name, RunId=run_id)
        state = status["JobRun"]["JobRunState"]
        if state in ("SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"):
            break
        print(f"  ...{job_name} is {state}, waiting {poll_seconds}s")
        time.sleep(poll_seconds)

    if state != "SUCCEEDED":
        reason = status["JobRun"].get("ErrorMessage", "no error message returned")
        raise RuntimeError(f"Glue job {job_name} ended in state {state}: {reason}")

    print(f"--- {job_name} SUCCEEDED ---")


def run_query(sql, label, poll_seconds=2):
    print(f"\n--- Running: {label} ---")
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_S3},
    )
    query_id = resp["QueryExecutionId"]

    while True:
        status = athena.get_query_execution(QueryExecutionId=query_id)
        state = status["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(poll_seconds)

    if state != "SUCCEEDED":
        reason = status["QueryExecution"]["Status"].get("StateChangeReason", "unknown error")
        raise RuntimeError(f"{label} FAILED: {reason}")

    print(f"--- {label} SUCCEEDED ---")


def run_ddl_file(table_name, path, poll_seconds=2):
    # DROP first rather than relying on the DDL's own `CREATE TABLE IF NOT
    # EXISTS`: if a table by this name already exists pointing at the wrong
    # S3 location (e.g. left over from manual/earlier setup), IF NOT EXISTS
    # silently no-ops and the stale definition keeps shadowing this table
    # forever. Dropping first guarantees the table always matches this DDL.
    run_query(f"DROP TABLE IF EXISTS {table_name}", f"DROP TABLE IF EXISTS {table_name}", poll_seconds)

    with open(path) as f:
        sql = f.read()
    run_query(sql, f"DDL {path}", poll_seconds)


def main():
    try:
        if RUN_INGEST_STEP:
            run_glue_job(GLUE_JOB_NAMES["ingest_api"])

        run_glue_job(GLUE_JOB_NAMES["clean_charge_raw"])
        run_glue_job(GLUE_JOB_NAMES["clean_data_import"])

        for table_name, ddl_path in DDL_STATEMENTS:
            run_ddl_file(table_name, ddl_path)

        run_glue_job(GLUE_JOB_NAMES["charge_type"])
        run_glue_job(GLUE_JOB_NAMES["build_charge_categorized"])
        run_glue_job(GLUE_JOB_NAMES["dashboard"])

        print("\n=== Pipeline completed successfully. ===")
        print("Check the dashboard page (index.html) -- it fetches summary.json")
        print("directly, so a hard refresh should show the current data.")
    except Exception as exc:
        print(f"\n=== Pipeline FAILED: {exc} ===", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
