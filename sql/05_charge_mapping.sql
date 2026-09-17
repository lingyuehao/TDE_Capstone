-- Table over glue_jobs/charge_type.py's output: one row per unique
-- (charge_type, charge_description) combination, with its assigned
-- major_category/subcategory/method. build_charge_categorized.py joins
-- charge_raw_clean to this table.

CREATE EXTERNAL TABLE IF NOT EXISTS charge_mapping (
  charge_type          string,
  charge_description   string,
  row_count            bigint,
  total_value          double,
  major_category       string,
  subcategory          string,
  method               string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('quoteChar'='"', 'separatorChar'=',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://capstone-tde-demo/curated/charge_mapping/'
TBLPROPERTIES ('skip.header.line.count'='1');

-- Note: charge_categorized has no static DDL file -- it's created (and
-- re-created) directly by build_charge_categorized.py via CTAS, since it
-- has to be dropped and rebuilt from scratch every time charge_mapping
-- changes, not just declared once.
