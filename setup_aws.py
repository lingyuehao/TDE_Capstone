"""
One-time (or re-run-when-changed) setup script: uploads this repo's Glue
job scripts + the shared taxonomy module to S3, then creates (or updates)
all 6 Glue Python Shell jobs via boto3 -- no console clicking. (One of
those 6, ingest-api, is a placeholder not yet triggered by
orchestrate.py -- see glue_jobs/ingest_api.py.)

This is separate from orchestrate.py on purpose: orchestrate.py TRIGGERS
runs of jobs that already exist; this script CREATES/UPDATES the jobs
themselves (their code location, job parameters, IAM role, etc.). Run
this whenever a script changes or you're setting the pipeline up for the
first time; run orchestrate.py to actually execute the pipeline.

Usage:
  python setup_aws.py

Requires AWS credentials configured, and an IAM role that Glue jobs can
assume (GLUE_ROLE_ARN below) with permissions for S3 (read/write on the
bucket) and Athena (for build_charge_categorized.py and dashboard.py,
which call the Athena API at runtime).
"""

import sys

import boto3

# ---------------------------------------------------------------------------
# Configuration -- edit these to match your environment
# ---------------------------------------------------------------------------
AWS_REGION = "us-east-1"
BUCKET = "capstone-tde-demo"
CODE_PREFIX = "code"  # scripts + taxonomy.py land under s3://<BUCKET>/<CODE_PREFIX>/

# The IAM role Glue jobs run as. Must already exist -- this script does
# not create IAM roles/policies, since those are account-security-sensitive
# and worth setting up deliberately rather than scripted blindly.
GLUE_ROLE_ARN = "arn:aws:iam::153106962329:role/glue-charge-categorization-role"

GLUE_VERSION = "3.0"
PYTHON_VERSION = "3.9"

ATHENA_DATABASE = "tde_demo"
ATHENA_OUTPUT_S3 = f"s3://{BUCKET}/athena-results/"

# Each job: local script path, S3 key (under CODE_PREFIX), job name, and
# the default arguments (job parameters) Glue will use unless overridden
# at run time. Matches the README's job-setup table.
JOBS = [
    {
        # Placeholder -- see glue_jobs/ingest_api.py's docstring. Included
        # here so `setup_aws.py` keeps the job's script/definition current
        # in Glue, but it's not wired into orchestrate.py's daily run yet
        # (RUN_INGEST_STEP there is False) since there's no real API/secret
        # behind API_ENDPOINT / API_SECRET_NAME.
        "name": "ingest-api",
        "script_path": "glue_jobs/ingest_api.py",
        "default_args": {
            "--API_ENDPOINT": "https://example.invalid/v1/charges",
            "--API_SECRET_NAME": "tde-demo/api-key",
            "--RAW_CHARGE_S3": f"s3://{BUCKET}/raw/trackingdetail.csv",
        },
        "extra_py_files": None,
        "additional_python_modules": None,
    },
    {
        "name": "clean-charge-raw",
        "script_path": "glue_jobs/clean_charge_raw.py",
        "default_args": {
            "--RAW_CHARGE_S3": f"s3://{BUCKET}/raw/trackingdetail.csv",
            "--CLEAN_OUTPUT_S3": f"s3://{BUCKET}/curated/charge_raw_clean/charge_raw_clean.csv",
        },
        "extra_py_files": None,
        "additional_python_modules": None,
    },
    {
        "name": "clean-data-import",
        "script_path": "glue_jobs/clean_data_import.py",
        "default_args": {
            "--RAW_DATA_IMPORT_S3": f"s3://{BUCKET}/raw/Data_Import_Table.csv",
            "--LOOKUP_OUTPUT_S3": f"s3://{BUCKET}/curated/data_import_lookup/data_import_lookup.csv",
        },
        "extra_py_files": None,
        "additional_python_modules": None,
    },
    {
        "name": "charge-type",
        "script_path": "glue_jobs/charge_type.py",
        "default_args": {
            "--RAW_S3_PATH": f"s3://{BUCKET}/curated/charge_raw_clean/charge_raw_clean.csv",
            "--MAPPING_S3_PATH": f"s3://{BUCKET}/curated/charge_mapping/charge_mapping.csv",
        },
        # taxonomy.py must be uploaded too (this script does that) and
        # attached here so `from taxonomy import ...` resolves at runtime.
        "extra_py_files": f"s3://{BUCKET}/{CODE_PREFIX}/taxonomy.py",
        "additional_python_modules": "pandas,scikit-learn",
    },
    {
        "name": "build-charge-categorized",
        "script_path": "glue_jobs/build_charge_categorized.py",
        "default_args": {
            "--ATHENA_DATABASE": ATHENA_DATABASE,
            "--ATHENA_OUTPUT_S3": ATHENA_OUTPUT_S3,
            "--CATEGORIZED_S3_PATH": f"s3://{BUCKET}/categorized/",
            "--RAW_TABLE": "charge_raw_clean",
            "--MAPPING_TABLE": "charge_mapping",
            "--OUTPUT_TABLE": "charge_categorized",
        },
        "extra_py_files": None,
        "additional_python_modules": None,
    },
    {
        "name": "dashboard",
        "script_path": "glue_jobs/dashboard.py",
        "default_args": {
            "--ATHENA_DATABASE": ATHENA_DATABASE,
            "--ATHENA_OUTPUT_S3": ATHENA_OUTPUT_S3,
            "--SUMMARY_S3_PATH": f"s3://{BUCKET}/dashboard/summary.json",
        },
        "extra_py_files": None,
        "additional_python_modules": None,
    },
]

s3 = boto3.client("s3", region_name=AWS_REGION)
glue = boto3.client("glue", region_name=AWS_REGION)


def upload_code():
    print("--- Uploading code to S3 ---")
    for job in JOBS:
        local_path = job["script_path"]
        s3_key = f"{CODE_PREFIX}/{local_path.split('/')[-1]}"
        s3.upload_file(local_path, BUCKET, s3_key)
        job["s3_script_location"] = f"s3://{BUCKET}/{s3_key}"
        print(f"  {local_path} -> {job['s3_script_location']}")

    taxonomy_key = f"{CODE_PREFIX}/taxonomy.py"
    s3.upload_file("src/taxonomy.py", BUCKET, taxonomy_key)
    print(f"  src/taxonomy.py -> s3://{BUCKET}/{taxonomy_key}")


def build_default_arguments(job):
    args = dict(job["default_args"])
    if job["additional_python_modules"]:
        args["--additional-python-modules"] = job["additional_python_modules"]
    if job["extra_py_files"]:
        args["--extra-py-files"] = job["extra_py_files"]
    return args


def create_or_update_job(job):
    job_def = {
        "Role": GLUE_ROLE_ARN,
        "Command": {
            "Name": "pythonshell",
            "ScriptLocation": job["s3_script_location"],
            "PythonVersion": PYTHON_VERSION,
        },
        "DefaultArguments": build_default_arguments(job),
        "GlueVersion": GLUE_VERSION,
        "MaxCapacity": 1.0,
    }

    existing = glue.list_jobs()["JobNames"]
    if job["name"] in existing:
        glue.update_job(JobName=job["name"], JobUpdate=job_def)
        print(f"  Updated job: {job['name']}")
    else:
        glue.create_job(Name=job["name"], **job_def)
        print(f"  Created job: {job['name']}")


def main():
    if "<ACCOUNT_ID>" in GLUE_ROLE_ARN:
        print(
            "ERROR: set GLUE_ROLE_ARN at the top of this script to your actual "
            "Glue execution role ARN before running.",
            file=sys.stderr,
        )
        sys.exit(1)

    upload_code()

    print("\n--- Creating/updating Glue jobs ---")
    for job in JOBS:
        create_or_update_job(job)

    print("\n=== Setup complete. Run orchestrate.py to execute the pipeline. ===")


if __name__ == "__main__":
    main()
