# TDE_Capstone

Charge-category classification for parcel-shipping data: notebooks that load the raw exports and rate sheets, build a category taxonomy, and classify charge line items against it.

## Structure

```
data/
  raw/         Raw database exports (tracking detail, invoice, etc.)
  samples/     Sample carrier rate files (UPS / FedEx net rates)
  scenarios/   Rerate scenario workbooks
notebooks/
  load_data.ipynb                  Initial exploration of the raw exports and rate files
  charge_categorization.ipynb      Builds the category taxonomy and classifies charge labels
  charge_categorization_check.ipynb  Reviews classification output, inspects what's still uncategorized
src/
  taxonomy.py  Shared taxonomy (categories, regex rules) imported by both categorization notebooks,
               so they can't drift out of sync
outputs/       Generated CSVs/HTML/pptx from the notebooks (category mappings, value counts, summaries)
Public_rate_sheet/  Published zone/rate reference sheets, unrelated to the notebook pipeline above
```

Notebooks are meant to be run from inside `notebooks/` (e.g. via Jupyter) -- they read from `../data/`, `../outputs/`, and import from `../src/taxonomy.py`.

## Related work

The AWS Glue + Athena production pipeline that runs this same categorization at scale lives on the `pipeline-demo` branch, not here -- see that branch's README for the architecture writeup.
