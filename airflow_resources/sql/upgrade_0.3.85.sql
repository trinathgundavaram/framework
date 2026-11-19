-- One-time upgrade to 0.3.85 (replace HSABC_DEV). Rename only: no row, column, type or index changes.
-- CMS_GOVT_RGLTRY_ExceptionsAudit -> GOVT_RGLTRY_ExceptionsAudit, so every framework table starts with GOVT_RGLTRY_.
-- Run once, with the DAGs paused, before deploying framework 0.3.85. Skip it where the table is already renamed
-- (step 0 returns only GOVT_RGLTRY_ExceptionsAudit). Afterwards airflow_resources/sql/schema_check.sql should return no rows.

-- Step 0: which name the table has now
SELECT DatabaseName, TableName FROM DBC.TablesV
WHERE DatabaseName = 'HSABC_DEV' AND TableName IN ('CMS_GOVT_RGLTRY_ExceptionsAudit', 'GOVT_RGLTRY_ExceptionsAudit');

-- Step 1: row count before
SELECT COUNT(*) AS Row_Cnt FROM HSABC_DEV.CMS_GOVT_RGLTRY_ExceptionsAudit;

-- Step 2: rename
RENAME TABLE HSABC_DEV.CMS_GOVT_RGLTRY_ExceptionsAudit TO HSABC_DEV.GOVT_RGLTRY_ExceptionsAudit;

-- Step 3: row count after (same as step 1)
SELECT COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_ExceptionsAudit;
