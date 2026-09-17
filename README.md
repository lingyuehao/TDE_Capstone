# TDE Charge Categorization Pipeline

Classifies raw parcel-shipping charge line items into a 7-category
taxonomy (+ catch-all) and publishes a summary dashboard, entirely from
AWS Glue Python Shell jobs + Athena -- no manual console steps.

## Why this pipeline exists in this shape

The original version of this pipeline had one Athena table
(`charge_categorized`) that was built by hand, once, via a `CREATE TABLE
AS SELECT` typed into the Athena console. Nothing regenerated it
automatically, so every time the category taxonomy changed, the
dashboard silently kept showing the old categories -- there was no code
path that would have caught or fixed that.

Separately, the raw charge export (`trackingdetail.csv`) has some rows
with embedded newlines inside quoted fields, which a plain line-based CSV
reader (including Athena's own CSV SerDe) can't parse correctly -- it
splits one logical record into multiple rows and shifts everything after
the break into the wrong columns. That silently corrupted ~99% of rows
when the raw file was read directly.

This rebuild fixes both problems by making every step of
raw-data -> dashboard a script, in a fixed order, with no step skipped
or done by hand.

## Pipeline steps

1. **Ingest** -- raw exports land in `s3://capstone-tde-demo/raw/`
   (`trackingdetail.csv`, `Data_Import_Table.csv`). Not scripted today;
   this is just wherever the exports get dropped. `glue_jobs/ingest_api.py`
   is a placeholder for automating this from a live API on a daily
   schedule -- see "Live data ingestion" below.
2. **`glue_jobs/clean_charge_raw.py`** -- fixes the embedded-newline
   problem in the charge export. Reads the raw file with Python's `csv`
   module (which correctly reassembles multi-line quoted fields),
   replaces any embedded newlines with a space, writes a clean
   single-line-per-row CSV to `curated/charge_raw_clean/`.
3. **`glue_jobs/clean_data_import.py`** -- same newline problem, the Data
   Import export. Also projects down to just
   `data_import_name`/`carrier_name`/`client_name` and dedupes to one row
   per import batch. Writes to `curated/data_import_lookup/`.
4. **DDL** (`sql/02_charge_raw_clean.sql`, `sql/04_data_import_lookup.sql`,
   `sql/05_charge_mapping.sql`) -- registers the Athena tables over the
   cleaned files. `orchestrate.py` drops each table before recreating it
   from its DDL file, rather than relying on `CREATE TABLE IF NOT EXISTS`
   -- a table that already exists (e.g. left over from earlier manual
   setup, possibly pointing at the wrong S3 location) would otherwise
   silently block the real DDL from ever taking effect.
5. **`glue_jobs/charge_type.py`** -- reads `charge_raw_clean`, gets unique
   `(charge_type, charge_description)` combinations, classifies each
   using the shared taxonomy (`src/taxonomy.py`) -- rule-based regex
   first, TF-IDF fuzzy match as a fallback. Writes `charge_mapping`.
6. **`glue_jobs/build_charge_categorized.py`** -- the step that used to be
   a manual Athena CTAS. Drops and rebuilds `charge_categorized` by
   joining `charge_raw_clean` to `charge_mapping`, via the Athena API
   (not the console). Unmatched rows default to `Other / Uncategorized`
   via `COALESCE` instead of showing up as an unlabeled `NULL` category.
7. **`glue_jobs/dashboard.py`** -- aggregates `charge_categorized` by
   category/subcategory, and (joined to `data_import_lookup`) by company
   and by carrier. Writes `dashboard/summary.json`.
8. **`dashboard/index.html`** -- static page, fetches `summary.json` from
   the same S3 folder (`fetch("summary.json", {cache: "no-store"})`) and
   renders it client-side. No build step.

## Running it

`orchestrate.py` runs steps 2 through 7 in order, via `boto3` (Glue +
Athena APIs) -- no console clicking required:

```bash
python orchestrate.py
```

This assumes:
- AWS credentials are configured (same account/role with access to the
  Glue jobs, Athena, and the S3 bucket)
- The Glue jobs already exist in Glue, named per `GLUE_JOB_NAMES` in
  `orchestrate.py` (`clean-charge-raw`, `clean-data-import`,
  `charge-type`, `build-charge-categorized`, `dashboard`) -- this script
  triggers runs of existing jobs, it doesn't create them

By default this runs 5 of the 6 Glue jobs `setup_aws.py` sets up --
`ingest-api` is skipped because `RUN_INGEST_STEP` in `orchestrate.py` is
`False` (see "Live data ingestion" below).

## Glue job setup (one-time, or whenever a script changes)

`setup_aws.py` does this end-to-end via `boto3` -- no console clicking:
uploads every `glue_jobs/*.py` script plus `src/taxonomy.py` to
`s3://capstone-tde-demo/code/`, then creates (or updates, if they already
exist) all 6 Glue jobs with the right script location, job parameters,
and IAM role.

```bash
python setup_aws.py
```

This is separate from `orchestrate.py` on purpose: `orchestrate.py`
*triggers runs* of jobs that already exist; `setup_aws.py` *creates/updates
the jobs themselves*. Run `setup_aws.py` first (and again any time a Glue
job script or the taxonomy changes), then `orchestrate.py` to execute the
pipeline.

Before running it, edit `GLUE_ROLE_ARN` near the top of `setup_aws.py` to
your account's Glue execution role -- that role needs S3 read/write on
the bucket and Athena access (for `build_charge_categorized.py` and
`dashboard.py`, which call the Athena API at runtime). Setup does not
create IAM roles/policies; those are account-security-sensitive and worth
setting up deliberately.

All 6 jobs are **Python Shell** jobs. For reference, here's what
`setup_aws.py` configures for each:

| Job name | Script | Job parameters | Extra |
|---|---|---|---|
| `ingest-api` | `glue_jobs/ingest_api.py` | `--API_ENDPOINT`, `--API_SECRET_NAME`, `--RAW_CHARGE_S3` | Placeholder -- not triggered by `orchestrate.py` by default, see "Live data ingestion" |
| `clean-charge-raw` | `glue_jobs/clean_charge_raw.py` | `--RAW_CHARGE_S3`, `--CLEAN_OUTPUT_S3` | -- |
| `clean-data-import` | `glue_jobs/clean_data_import.py` | `--RAW_DATA_IMPORT_S3`, `--LOOKUP_OUTPUT_S3` | -- |
| `charge-type` | `glue_jobs/charge_type.py` | `--RAW_S3_PATH`, `--MAPPING_S3_PATH` | `--extra-py-files s3://.../code/taxonomy.py`, `--additional-python-modules pandas,scikit-learn` |
| `build-charge-categorized` | `glue_jobs/build_charge_categorized.py` | `--ATHENA_DATABASE`, `--ATHENA_OUTPUT_S3`, `--CATEGORIZED_S3_PATH`, `--RAW_TABLE`, `--MAPPING_TABLE`, `--OUTPUT_TABLE` | -- |
| `dashboard` | `glue_jobs/dashboard.py` | `--ATHENA_DATABASE`, `--ATHENA_OUTPUT_S3`, `--SUMMARY_S3_PATH` | -- |

`src/taxonomy.py` is uploaded to S3 by `setup_aws.py` and attached to the
`charge-type` job via `--extra-py-files` -- that's what makes `from
taxonomy import ...` resolve inside that job, and keeps the taxonomy as a
single source of truth instead of copy-pasted into the job script.

## Live data ingestion (placeholder)

Today, "ingest" means someone manually drops export files into
`s3://capstone-tde-demo/raw/`. `glue_jobs/ingest_api.py` is a placeholder
for replacing that with a daily pull from a live API, landing data in the
same `raw/` location so the rest of the pipeline picks it up unchanged --
nothing about `clean_charge_raw.py` onward needs to know or care whether
`raw/` was populated by hand or by this job.

Nothing here is live yet:
- `fetch_from_api()` in `ingest_api.py` raises `NotImplementedError` on
  purpose -- there's no real endpoint or auth wired up.
- `RUN_INGEST_STEP` in `orchestrate.py` is `False`, so `orchestrate.py`
  doesn't call this job even though `setup_aws.py` creates it in Glue.
- No recurring trigger (e.g. an EventBridge Scheduler rule) is deployed.

To go live:
1. Implement `fetch_from_api()` with the real endpoint and auth, and set
   real values for `--API_ENDPOINT` / `--API_SECRET_NAME` in
   `setup_aws.py`'s `JOBS` list (storing the actual key/token in Secrets
   Manager under that secret name).
2. Set `RUN_INGEST_STEP = True` in `orchestrate.py`.
3. Add a daily trigger -- the natural fit is an EventBridge Scheduler
   rule (cron, once a day) invoking a small Lambda that calls
   `glue.start_job_run` for `ingest-api` and then runs the same sequence
   `orchestrate.py` already does (or just runs `orchestrate.py` itself
   somewhere schedulable, e.g. as a container task). Once that fires, the
   dashboard updates itself: `dashboard.py`'s existing
   `dashboard/summary.json` rewrite already reflects whatever's currently
   in `charge_categorized` -- no separate "refresh the dashboard" step is
   needed beyond the pipeline re-running.

## S3 layout

```
s3://capstone-tde-demo/
  raw/                        <- raw exports land here, untouched
    trackingdetail.csv
    Data_Import_Table.csv
  curated/
    charge_raw_clean/         <- clean_charge_raw.py output
    data_import_lookup/       <- clean_data_import.py output
    charge_mapping/           <- charge_type.py output
  categorized/                <- build_charge_categorized.py output (Parquet)
  dashboard/
    summary.json              <- dashboard.py output
    index.html                <- static page (uploaded manually or via CI)
  athena-results/             <- Athena query result staging
  code/
    taxonomy.py               <- attached to charge-type via --extra-py-files
```

## Changing the taxonomy

1. Edit `src/taxonomy.py`
2. Run `setup_aws.py` to re-upload it to `s3://capstone-tde-demo/code/taxonomy.py`
3. Run `orchestrate.py` (or just the `charge-type` ->
   `build-charge-categorized` -> `dashboard` jobs, in that order)

There is no step that can be skipped here -- `charge_mapping` alone does
not affect the dashboard until `charge_categorized` is rebuilt from it,
and `charge_categorized` alone does not affect the dashboard until
`dashboard.py` re-aggregates it.

## Known data-quality note

A small number of rows (tens, out of tens of millions) have
non-English/rare-phrasing charge descriptions not yet covered by the
taxonomy, and a similarly small number have non-numeric junk in
`charge_value` in the source export. Both are handled gracefully
(`COALESCE`/`TRY_CAST` land them in `Other / Uncategorized` /
`NULL`-safe totals rather than failing the pipeline) rather than fixed at
the source, since they're a rounding error against the total row count.

## Related work

The exploratory notebooks this pipeline's taxonomy and logic grew out of
(`load_data.ipynb`, `charge_categorization.ipynb`,
`charge_categorization_check.ipynb`) live on the `main` branch under
`notebooks/`, alongside the raw sample data (`data/`) and generated
review output (`outputs/`) -- see that branch's README for their
structure. This branch's `src/taxonomy.py` and `main`'s are kept in sync
by hand; they're logically the same taxonomy, just documented for their
respective contexts (this pipeline vs. the notebooks).
