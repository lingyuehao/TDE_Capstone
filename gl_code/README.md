# GL Code

Fills in the GL code (column M) on UPS billing files for CooperSurgical (CSI) and Belimo. Rows the rules aren't confident about are marked `review` for a person to check.

The UPS data files are not in this repo. Get them from the team's shared folder.

## Files

| File | What it's for |
|---|---|
| `GL_Code_Apply.ipynb` | **Run this one on new UPS files.** It fills column M and writes a review sheet. |
| `GL_Code_Automation.ipynb` | Shows how the rules were built: the doc rules (v1) are checked against an answer key, then improved (v2). Only needed when changing the rules. |
| `input/` | Put the UPS csv files here (empty in the repo). |
| `output/` | Results get written here. |
| `history/` | Tracking # -> GL history, used across billing periods (optional). |

## Setup

```
pip install pandas numpy openpyxl jupyter
```

Run the notebooks from inside this `gl_code/` folder, because they use the relative paths `input/`, `output/` and `history/`.

## How to run `GL_Code_Apply.ipynb`

1. Copy the new UPS csv files into `input/`. Use them exactly as exported from UPS: no header row, and column M can be blank.
   - The file name must contain `CSI`, `Cooper` or `Belimo`, so the notebook knows which company's rules to use.
   - Example: `UPS_GL_Code_Logic_Not_Completed_CSI_100226.csv`
2. Run all cells.
3. Check `output/`. For each input file you get:
   - `<name>_GL.csv`: the original file with column M filled in. Rows that need a person get the value `review`.
   - `<name>_GL_review.xlsx`, with three sheets:
     - `To_Review`: rows to check, with the suggested GL and the reason
     - `All_Rows`: every row
     - `Summary`: row counts by GL code

Optional cells at the end of the notebook:

- **Check against an answer key.** Runs automatically if a matching `..._Completed_...csv` file (same name, with `Not_Completed` changed to `Completed`) is also in `input/`. It prints the accuracy.
- **Write manual answers back.** Fill the `Final_GL` column in the `To_Review` sheet and save the file. Then set `APPLY_MANUAL = True` and run the cell. The answers are copied into `<name>_GL.csv`.
- **Save tracking history.** After the results are checked, set `UPDATE_LEDGER = True` and run the cell. Later bills with shipping corrections for the same tracking # will then get the same GL.

## How to run `GL_Code_Automation.ipynb`

You need both files for each company in `input/`:

- the original: `..._Not_Completed_<CSI|Belimo>_....csv`
- the answer key: `..._Completed_<CSI|Belimo>_....csv`

Run all cells. The notebook compares v1 and v2 accuracy and writes the rows that are still wrong to `output/<company>_v2_wrong_rows.csv`.

## Changing the rules

The rules are in the `RULES_V2` cell. **That cell is copied into both notebooks**, so if you change a rule, change it in both.

Each rule is a list of conditions in the form `(column, op, value)`, and all the conditions have to match:

- `column` is an Excel column letter. `"P|Q"` means P or Q.
- `op` is one of `eq`, `in`, `contains`, `word`, `contains_any`, `not_contains_any`, `eq_col` or `ne_col`.
- When more than one rule matches a row, the higher `layer` wins:
  - layer 0: the default
  - layers 1-2: direction and site
  - layer 3: charge type
  - layers 4 and up: purpose (department, person, reference)

The review triggers are set in the `assign_gl` cell:

- `KNOWN_ACCOUNTS`
- `LOW_CONFIDENCE_RULES`
- `WEAK_TRACKING_SOURCES`
