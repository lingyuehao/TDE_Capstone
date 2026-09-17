-- Table over glue_jobs/clean_data_import.py's output: one row per import
-- batch (data_import_name), with its carrier and client name. This is
-- what dashboard.py joins charge_categorized to for the per-company and
-- per-carrier breakdowns.

CREATE EXTERNAL TABLE IF NOT EXISTS data_import_lookup (
  data_import_name   string,
  carrier_name        string,
  client_name          string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('quoteChar'='"', 'separatorChar'=',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://capstone-tde-demo/curated/data_import_lookup/'
TBLPROPERTIES ('skip.header.line.count'='1');
