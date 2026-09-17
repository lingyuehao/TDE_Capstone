-- Table over glue_jobs/clean_charge_raw.py's output. This is what the
-- rest of the pipeline actually reads from -- charge_raw (01) is broken
-- for ~99% of rows because of embedded newlines in the source export.

CREATE EXTERNAL TABLE IF NOT EXISTS charge_raw_clean (
  tde_tracking_detail_id       string,
  tracking_detail_name         string,
  currency                     string,
  created_date                 string,
  created_by_full_name         string,
  last_modified_date           string,
  last_modified_by_full_name   string,
  last_activity_date           string,
  ar_name                      string,
  charge_description           string,
  charge_type                  string,
  charge_value                 string,
  data_import_name             string,
  migration_info               string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('quoteChar'='"', 'separatorChar'=',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://capstone-tde-demo/curated/charge_raw_clean/'
TBLPROPERTIES ('skip.header.line.count'='1');
