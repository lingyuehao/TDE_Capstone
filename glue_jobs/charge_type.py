"""
Step 3 of the pipeline: categorize unique charge labels into charge_mapping.

Glue Python Shell job.

Reads the CLEANED charge file (output of clean_charge_raw.py -- not the
raw export directly, since that has the embedded-newline/column-shift
problem clean_charge_raw.py fixes). Gets the unique (Charge Type, Charge
Description) combinations, classifies each with the shared taxonomy
(src/taxonomy.py -- rule-based first, TF-IDF fuzzy fallback second), and
writes the resulting mapping table to S3.

Classification runs on unique (Charge Type, Charge Description)
combinations, not per row -- there are only a few thousand of those even
at tens of millions of source rows, so re-running this after a taxonomy
change is fast.

taxonomy.py must be attached to this job via the Glue job's
"--extra-py-files" parameter, pointing at wherever src/taxonomy.py is
uploaded in S3 (e.g. s3://capstone-tde-demo/code/taxonomy.py) -- that's
what makes `from taxonomy import ...` resolve at runtime, and it's what
keeps the taxonomy as a single source of truth instead of copy-pasted
into this job.

Downstream: sql/05_charge_mapping.sql creates the Athena table over this
job's output; build_charge_categorized.py joins charge_raw_clean to it.

Job parameters (set in the Glue job's "Job parameters" field):
  --RAW_S3_PATH       e.g. s3://capstone-tde-demo/curated/charge_raw_clean/charge_raw_clean.csv
  --MAPPING_S3_PATH   e.g. s3://capstone-tde-demo/curated/charge_mapping/charge_mapping.csv

Additional job parameters needed:
  --extra-py-files            s3://capstone-tde-demo/code/taxonomy.py
  --additional-python-modules pandas,scikit-learn
"""

import io
import sys

import boto3
import pandas as pd
from awsglue.utils import getResolvedOptions
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from taxonomy import CATCH_ALL, TAXONOMY, TFIDF_EXCLUDE, rule_classify_row

args = getResolvedOptions(sys.argv, ["RAW_S3_PATH", "MAPPING_S3_PATH"])
RAW_S3_PATH = args["RAW_S3_PATH"]
MAPPING_S3_PATH = args["MAPPING_S3_PATH"]

s3 = boto3.client("s3")


def parse_s3_path(s3_path):
    assert s3_path.startswith("s3://"), f"Not an s3:// path: {s3_path}"
    bucket, key = s3_path[5:].split("/", 1)
    return bucket, key


def read_csv_from_s3(s3_path, **kwargs):
    bucket, key = parse_s3_path(s3_path)
    obj = s3.get_object(Bucket=bucket, Key=key)
    return pd.read_csv(io.BytesIO(obj["Body"].read()), **kwargs)


def write_csv_to_s3(df, s3_path):
    bucket, key = parse_s3_path(s3_path)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())


# ---------------------------------------------------------------------------
# Load the cleaned charge-level data
# ---------------------------------------------------------------------------
detail = read_csv_from_s3(
    RAW_S3_PATH,
    usecols=["charge_type", "charge_description", "charge_value"],
    low_memory=False,
)
detail = detail.rename(columns={
    "charge_type": "Charge Type",
    "charge_description": "Charge Description",
    "charge_value": "Charge Value",
})
detail["Charge Value"] = pd.to_numeric(detail["Charge Value"], errors="coerce")
print(f"Loaded {len(detail):,} rows from {RAW_S3_PATH}")

n_major = len(TAXONOMY) + 1  # + catch-all
print(f"{n_major} major categories (incl. catch-all)")
for major, subcats in TAXONOMY.items():
    print(f"  {major}: {len(subcats)} subcategories")

# ---------------------------------------------------------------------------
# Apply to unique (Charge Type, Charge Description) combinations
#
# Classifying at the unique-combination level (not per row) is what keeps
# this fast and reusable at any data volume.
# ---------------------------------------------------------------------------
unique_labels = (
    detail.groupby(["Charge Type", "Charge Description"], dropna=False)["Charge Value"]
    .agg(row_count="size", total_value="sum")
    .reset_index()
)
print(f"{len(unique_labels):,} unique (Charge Type, Charge Description) combinations covering {unique_labels['row_count'].sum():,} rows")

classified = unique_labels.apply(lambda r: rule_classify_row(r["Charge Type"], r["Charge Description"]), axis=1)
unique_labels["Major Category"] = [c[0] if c else None for c in classified]
unique_labels["Subcategory"] = [c[1] if c else None for c in classified]
unique_labels["Method"] = ["rule" if c else None for c in classified]

matched_rows = unique_labels.loc[unique_labels["Major Category"].notna(), "row_count"].sum()
print(f"Rule-based coverage: {matched_rows / unique_labels['row_count'].sum() * 100:.1f}% of rows")

# ---------------------------------------------------------------------------
# TF-IDF fallback for labels the rules missed
# ---------------------------------------------------------------------------
ref_docs, ref_labels = [], []
for major, subcats in TAXONOMY.items():
    for sub, patterns in subcats.items():
        if (major, sub) in TFIDF_EXCLUDE:
            continue
        doc = " ".join(p.replace(r"\b", "").replace(".*", " ").replace("'?", "").replace("?", "") for p in patterns)
        ref_docs.append(doc)
        ref_labels.append((major, sub))

vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
ref_vectors = vectorizer.fit_transform(ref_docs)

SIMILARITY_THRESHOLD = 0.25


def normalize(text):
    if pd.isna(text):
        return ""
    return str(text).lower().strip()


def fallback_classify_row(charge_type, charge_desc):
    t = (normalize(charge_type) + " " + normalize(charge_desc)).strip()
    if not t:
        return None
    vec = vectorizer.transform([t])
    sims = cosine_similarity(vec, ref_vectors)[0]
    best_idx = sims.argmax()
    if sims[best_idx] >= SIMILARITY_THRESHOLD:
        return ref_labels[best_idx]
    return None


unmatched = unique_labels["Major Category"].isna()
for idx in unique_labels.index[unmatched]:
    row = unique_labels.loc[idx]
    result = fallback_classify_row(row["Charge Type"], row["Charge Description"])
    if result:
        unique_labels.loc[idx, "Major Category"] = result[0]
        unique_labels.loc[idx, "Subcategory"] = result[1]
        unique_labels.loc[idx, "Method"] = "tfidf_fallback"

unique_labels["Major Category"] = unique_labels["Major Category"].fillna(CATCH_ALL[0])
unique_labels["Subcategory"] = unique_labels["Subcategory"].fillna(CATCH_ALL[1])
unique_labels["Method"] = unique_labels["Method"].fillna("none")

final_coverage = 1 - unique_labels.loc[unique_labels["Major Category"] == CATCH_ALL[0], "row_count"].sum() / unique_labels["row_count"].sum()
print(f"Final coverage after TF-IDF fallback: {final_coverage * 100:.1f}% of rows categorized")
print(unique_labels["Method"].value_counts())

# ---------------------------------------------------------------------------
# Save the reusable mapping table to S3, with lowercase column names so it
# joins cleanly against charge_raw_clean (which also uses lowercase names).
# ---------------------------------------------------------------------------
unique_labels = unique_labels.rename(columns={
    "Charge Type": "charge_type",
    "Charge Description": "charge_description",
    "Major Category": "major_category",
    "Subcategory": "subcategory",
    "Method": "method",
})
write_csv_to_s3(unique_labels, MAPPING_S3_PATH)
print(f"Saved mapping table to {MAPPING_S3_PATH}")
