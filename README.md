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
   (`trackingdetail.csv`, `Data_Import_Table.csv`). Not scripted; this is
   just wherever the exports get dropped.
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
   cleaned files. Run once; safe to re-run (`CREATE TABLE IF NOT EXISTS`).
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
- The 5 Glue jobs already exist in Glue, named per `GLUE_JOB_NAMES` in
  `orchestrate.py` (`clean-charge-raw`, `clean-data-import`,
  `charge-type`, `build-charge-categorized`, `dashboard`) -- this script
  triggers runs of existing jobs, it doesn't create them

## Glue job setup (one-time, per job)

Each job needs its script uploaded and its job parameters set. All of
them are **Python Shell** jobs.

| Job name | Script | Job parameters | Extra |
|---|---|---|---|
| `clean-charge-raw` | `glue_jobs/clean_charge_raw.py` | `--RAW_CHARGE_S3`, `--CLEAN_OUTPUT_S3` | -- |
| `clean-data-import` | `glue_jobs/clean_data_import.py` | `--RAW_DATA_IMPORT_S3`, `--LOOKUP_OUTPUT_S3` | -- |
| `charge-type` | `glue_jobs/charge_type.py` | `--RAW_S3_PATH`, `--MAPPING_S3_PATH` | `--extra-py-files s3://.../code/taxonomy.py`, `--additional-python-modules pandas,scikit-learn` |
| `build-charge-categorized` | `glue_jobs/build_charge_categorized.py` | `--ATHENA_DATABASE`, `--ATHENA_OUTPUT_S3`, `--CATEGORIZED_S3_PATH`, `--RAW_TABLE`, `--MAPPING_TABLE`, `--OUTPUT_TABLE` | -- |
| `dashboard` | `glue_jobs/dashboard.py` | `--ATHENA_DATABASE`, `--ATHENA_OUTPUT_S3`, `--SUMMARY_S3_PATH` | -- |

`src/taxonomy.py` must be uploaded to S3 separately (e.g.
`s3://capstone-tde-demo/code/taxonomy.py`) and attached to the
`charge-type` job via `--extra-py-files` -- that's what makes
`from taxonomy import ...` resolve inside that job, and keeps the
taxonomy as a single source of truth instead of copy-pasted into the job
script.

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
2. Re-upload it to `s3://capstone-tde-demo/code/taxonomy.py`
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
