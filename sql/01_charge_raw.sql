-- Reference only: this is the table definition over the RAW, uncleaned
-- charge export. It is not used by any job in this pipeline -- it's kept
-- here so the original data-quality problem (embedded newlines shifting
-- columns) is documented and reproducible if anyone needs to see it.
-- The pipeline reads from charge_raw_clean (02_charge_raw_clean.sql)
-- instead, which is built by glue_jobs/clean_charge_raw.py.

CREATE EXTERNAL TABLE IF NOT EXISTS charge_raw (
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
LOCATION 's3://capstone-tde-demo/raw/'
TBLPROPERTIES ('skip.header.line.count'='1');
