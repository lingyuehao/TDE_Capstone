-- Reference only: table over the RAW Data Import export, before
-- clean_data_import.py's projection + dedup. Not read by any job in
-- this pipeline -- dashboard.py reads data_import_lookup
-- (04_data_import_lookup.sql) instead.

CREATE EXTERNAL TABLE IF NOT EXISTS data_import_raw (
  data_import_name   string,
  carrier_name        string,
  client_name          string
  -- ... plus whatever other columns the raw Data Import export has
  -- (FieldMapping JSON, Document URL, etc.) -- not needed downstream,
  -- so not enumerated here. See clean_data_import.py for the exact
  -- source header it reads.
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('quoteChar'='"', 'separatorChar'=',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://capstone-tde-demo/raw/'
TBLPROPERTIES ('skip.header.line.count'='1');
