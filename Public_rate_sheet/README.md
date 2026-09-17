# Zone2-8_rate_sheet.csv

## What this file is

A unified table of **FedEx and UPS published list rates** for U.S. domestic shipping, restricted to **zones 2–8** — the standard distance-based zones both carriers define the same way (zone 2 = closest to origin, zone 8 = farthest). Because both carriers use this zone logic consistently, this is the file you use to **compare rates across carriers for the same service and same zone**.
> Rates effective **1/5/2026**. Source files: FedEx CSV rate sheets and `UPS_daily-rates-us-en.xlsx`.
For rates to Hawaii, Alaska, Canada, and Puerto Rico, see `extended_rates.csv` instead — those zones aren't numbered consistently across carriers or even across services from the same carrier, so they can't be safely matched by zone number the way this file can.

### Notice

- **`SAME_DAY`** tier is FedEx-only (35 rows, zones 2–8, per-lb freight rates only).
- **`GROUND`** includes both FedEx Ground and FedEx Home Delivery as separate `service_name` rows (identical base rates in this data) — filter on `service_name` if you need to tell them apart, or just use `service_tier` if you want them treated as one bucket.

--- 
## Columns

| Column | Description |
|---|---|
| `carrier` | `FedEx` or `UPS` |
| `service_name` | The carrier's actual product name (e.g. "FedEx Priority Overnight", "UPS Next Day Air") |
| `service_tier` | **Normalized speed category** — use this to match equivalent services across carriers (see table below). Different carriers never share the same `service_name`, but equivalent products share the same `service_tier`. |
| `zone` | Zone number, 2–8 |
| `weight_min_lbs` / `weight_max_lbs` | The weight range this rate applies to (see below) |
| `rate_type` | `flat_per_package` or `per_lb` (see below) |
| `rate_usd` | The dollar rate |
| `effective_date` | Rate effective date |
| `source_file` | Which original file this row came from |
---

## `service_tier` — how services map across carriers

Since FedEx and UPS never share product names, use `service_tier` to compare equivalents:

| service_tier | FedEx | UPS |
|---|---|---|
| `SAME_DAY` | FedEx SameDay Freight | *(no UPS equivalent in this data)* |
| `EARLY_AM_OVERNIGHT` | FedEx First Overnight | UPS Next Day Air Early |
| `MORNING_OVERNIGHT` | FedEx Priority Overnight | UPS Next Day Air |
| `EOD_OVERNIGHT` | FedEx Standard Overnight | UPS Next Day Air Saver |
| `2DAY_AM` | FedEx 2Day AM | UPS 2nd Day Air A.M. |
| `2DAY` | FedEx 2Day | UPS 2nd Day Air |
| `3DAY` | FedEx Express Saver | UPS 3 Day Select |
| `GROUND` | FedEx Ground, FedEx Home Delivery | UPS Ground |

---

## `rate_type` — flat vs. per-pound

**`flat_per_package`** → `weight_min_lbs` = `weight_max_lbs` (a single discrete weight). `rate_usd` is the **total price** for one package at that exact weight.

```
zone=2, weight_min=5, weight_max=5, rate=29.71  →  a 5 lb package to zone 2 costs $29.71 total
```

**`per_lb`** → `weight_min_lbs`/`weight_max_lbs` form a **range** (max may be blank, meaning "and up"). `rate_usd` is a **per-pound multiplier** — multiply by the total shipment weight to get the cost. This tier only kicks in for heavy/bulk shipments (typically 100–150+ lbs) — normal parcels almost never hit this.

```
zone=4, weight_min=150, weight_max=(blank), rate=0.88  →  a 200 lb shipment costs 200 × $0.88 = $176.00
```

---


## Example queries

**Compare the same service/zone/weight across carriers:**
```python
import pandas as pd
df = pd.read_csv('core_rates.csv')

df[(df.service_tier == 'GROUND') & (df.zone == 5) & (df.weight_min_lbs == 10)]
```

**Find the cheapest carrier for a specific shipment:**
```python
zone, weight = 6, 3
match = df[
    (df.rate_type == 'flat_per_package') &
    (df.zone == zone) &
    (df.weight_min_lbs == weight)
]
cheapest = match.sort_values('rate_usd').groupby('service_tier').first()
print(cheapest[['carrier', 'service_name', 'rate_usd']])
```

**Calculate a per-lb (bulk) shipment cost:**
```python
def per_lb_cost(df, service_tier, carrier, zone, total_weight):
    rows = df[
        (df.service_tier == service_tier) & (df.carrier == carrier) &
        (df.zone == zone) & (df.rate_type == 'per_lb') &
        (df.weight_min_lbs <= total_weight) &
        ((df.weight_max_lbs >= total_weight) | df.weight_max_lbs.isna())
    ]
    if rows.empty:
        return None
    rate = rows.iloc[0]['rate_usd']
    return round(rate * total_weight, 2)

per_lb_cost(df, 'GROUND', 'UPS', zone=4, total_weight=200)  # -> $176.00
```

---

