-- Protocol version cutover of a project's changed tables (replace HSABC_DEV). The framework table name (Table_Nm)
-- stays; the staging, core and export tables get new names, and the batches from the cutover date carry the new
-- compliance version. Tables that do not change are not touched and keep the old version.
-- Replace every {{...}} before running (search for "{{"):
--   {{PROJECT}}        project code                          CMSUNIV
--   {{TABLES}}         the changed tables, quoted            'ODAG1','ODAG3'
--   {{OLD_VRSN}}       current compliance version            2200
--   {{NEW_VRSN}}       new compliance version                2300
--   {{OLD_FILE_VRSN}}  current file version                  22
--   {{NEW_FILE_VRSN}}  new file version                      23
--   {{OLD_TAG}}        part of the table names that changes  2022
--   {{NEW_TAG}}        what it becomes                       2023
--   {{CUTOVER_DT}}     first batch creation date of the new version, YYYY-MM-DD
-- A batch takes the version effective on the date it is created, not on its report period.
-- New names = the current names with {{OLD_TAG}} replaced by {{NEW_TAG}} (staging, core, export tables, the
-- table-named columns of ComplianceTargetColumnConfig, and the source schema / tables in the query of a query
-- source, e.g. Staging.cms2022.ODAG_Table3_2022). Check them with Part 0 first.

-- ---------------------------------------------------------------------------------------------------------------
-- Part 0: preview (changes nothing)

-- File sources: new staging / core tables, and whether the file name template has {VERSION}. Without {VERSION}
-- an old and a new file would match both configs, so Part 1 adds such a config inactive and Part 2 switches it on.
SELECT Table_Nm, Src_ID, File_Vrsn_Cd, Src_File_Nm_Tmplt,
       CASE WHEN POSITION('{VERSION' IN UPPER(Src_File_Nm_Tmplt)) > 0 THEN 'Y' ELSE 'N' END AS Has_Version_Token,
       Stg_Table_Nm, OREPLACE(Stg_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Stg_Table_Nm,
       Core_Table_Nm, OREPLACE(Core_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Core_Table_Nm
FROM HSABC_DEV.ComplianceSourceFileConfig
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND File_Vrsn_Cd = '{{OLD_FILE_VRSN}}' AND Active_Ind = 1
ORDER BY Table_Nm, Src_ID;

-- Query sources: new staging / core tables and the new query. Every {{OLD_TAG}} in the query is replaced (schema,
-- tables, ID columns); check that no other text holds it (a date literal, a filter value).
SELECT Table_Nm, Src_ID,
       Stg_Table_Nm, OREPLACE(Stg_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Stg_Table_Nm,
       Core_Table_Nm, OREPLACE(Core_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Core_Table_Nm,
       Src_Sql_Txt, OREPLACE(Src_Sql_Txt, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Src_Sql_Txt
FROM HSABC_DEV.ComplianceInternalSourceConfig
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Active_Ind = 1
ORDER BY Table_Nm, Src_ID;

-- Export tables
SELECT Table_Nm, Exp_Table_Nm, OREPLACE(Exp_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Exp_Table_Nm,
       OREPLACE(Exp_Proc_Parm_Txt, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Exp_Proc_Parm_Txt, Exp_Sql_Txt, Exp_Pre_Sql_Txt, Exp_Post_Sql_Txt
FROM HSABC_DEV.ComplianceExportConfig
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}})
ORDER BY Table_Nm;

-- Table-named columns (ID / key columns)
SELECT Table_Nm, Column_Nm, OREPLACE(Column_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}') AS New_Column_Nm, Value_Cd, Value_Txt
FROM HSABC_DEV.ComplianceTargetColumnConfig
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Column_Nm LIKE '%{{OLD_TAG}}%' AND Active_Ind = 1
ORDER BY Table_Nm, Column_Nm;

-- Crosswalk rows that move to the new version
SELECT Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn
FROM HSABC_DEV.ComplianceDataSetSourceXwalk
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Cmplnc_Vrsn = '{{OLD_VRSN}}'
  AND Effective_End_Dt_Key IS NULL AND Active_Ind = 1
ORDER BY Table_Nm, Src_ID, Run_Ty;

-- ---------------------------------------------------------------------------------------------------------------
-- Part 1: before the cutover. The new staging, core and export tables must already exist.
-- Safe to run early: the crosswalk dates start the new batches on {{CUTOVER_DT}}, and a file with the new version
-- in its name matches only the new file configs. Rerunning adds nothing twice.

-- 1.1 Crosswalk: the new version from the cutover date, the old one ends the day before
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn,
   Rpt_Period_Cd, Rpt_Dt_Sql_Txt, Include_Ind, Active_Ind)
SELECT x.Project_Cd, x.Table_Nm, x.Src_ID, x.Run_Ty, DATE '{{CUTOVER_DT}}', NULL, '{{NEW_VRSN}}',
       x.Rpt_Period_Cd, x.Rpt_Dt_Sql_Txt, x.Include_Ind, x.Active_Ind
FROM HSABC_DEV.ComplianceDataSetSourceXwalk x
WHERE x.Project_Cd = '{{PROJECT}}' AND x.Table_Nm IN ({{TABLES}}) AND x.Cmplnc_Vrsn = '{{OLD_VRSN}}'
  AND x.Effective_End_Dt_Key IS NULL AND x.Active_Ind = 1
  AND x.Effective_Start_Dt_Key < DATE '{{CUTOVER_DT}}'
  AND NOT EXISTS (SELECT 1 FROM HSABC_DEV.ComplianceDataSetSourceXwalk n
                  WHERE n.Project_Cd = x.Project_Cd AND n.Table_Nm = x.Table_Nm AND n.Src_ID = x.Src_ID
                    AND n.Run_Ty = x.Run_Ty AND n.Effective_Start_Dt_Key = DATE '{{CUTOVER_DT}}');

UPDATE HSABC_DEV.ComplianceDataSetSourceXwalk
SET Effective_End_Dt_Key = DATE '{{CUTOVER_DT}}' - 1, LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Cmplnc_Vrsn = '{{OLD_VRSN}}'
  AND Effective_End_Dt_Key IS NULL AND Active_Ind = 1
  AND Effective_Start_Dt_Key < DATE '{{CUTOVER_DT}}';

-- 1.2 File configs of the new file version, on the new staging / core tables (active only with {VERSION})
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig
  (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, File_Vrsn_Cd, Delmtr_Cd, Src_File_Has_Hdr_Ind,
   Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm,
   Core_Table_Nm, Active_Ind)
SELECT c.Project_Cd, c.Table_Nm, c.Src_ID, c.Src_File_Nm_Tmplt, '{{NEW_FILE_VRSN}}', c.Delmtr_Cd, c.Src_File_Has_Hdr_Ind,
       c.Src_File_Has_Trlr_Ind, c.Allow_Zero_Rcd_Ind, c.Src_File_Path,
       c.Stg_Schema_Nm, OREPLACE(c.Stg_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}'),
       c.Core_Schema_Nm, OREPLACE(c.Core_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}'),
       CASE WHEN POSITION('{VERSION' IN UPPER(c.Src_File_Nm_Tmplt)) > 0 THEN 1 ELSE 0 END
FROM HSABC_DEV.ComplianceSourceFileConfig c
WHERE c.Project_Cd = '{{PROJECT}}' AND c.Table_Nm IN ({{TABLES}}) AND c.File_Vrsn_Cd = '{{OLD_FILE_VRSN}}'
  AND c.Active_Ind = 1
  AND NOT EXISTS (SELECT 1 FROM HSABC_DEV.ComplianceSourceFileConfig n
                  WHERE n.Project_Cd = c.Project_Cd AND n.Table_Nm = c.Table_Nm AND n.Src_ID = c.Src_ID
                    AND n.File_Vrsn_Cd = '{{NEW_FILE_VRSN}}');

-- 1.3 Table-named columns of the new tables (a row for a column a table does not have is ignored)
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
SELECT t.Project_Cd, t.Table_Nm, OREPLACE(t.Column_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}'), t.Value_Cd, t.Value_Txt, 1
FROM HSABC_DEV.ComplianceTargetColumnConfig t
WHERE t.Project_Cd = '{{PROJECT}}' AND t.Table_Nm IN ({{TABLES}}) AND t.Column_Nm LIKE '%{{OLD_TAG}}%'
  AND t.Active_Ind = 1
  AND NOT EXISTS (SELECT 1 FROM HSABC_DEV.ComplianceTargetColumnConfig n
                  WHERE n.Project_Cd = t.Project_Cd AND n.Table_Nm = t.Table_Nm
                    AND n.Column_Nm = OREPLACE(t.Column_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}'));

-- ---------------------------------------------------------------------------------------------------------------
-- Part 2: on the cutover day, after the last old-version files are loaded and their batches exported.
-- These rows have no version, so they switch here. Run as one transaction (BT; ... ET; in Teradata mode).

-- 2.1 File configs: old version off, new version on
UPDATE HSABC_DEV.ComplianceSourceFileConfig
SET Active_Ind = 0, LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND File_Vrsn_Cd = '{{OLD_FILE_VRSN}}' AND Active_Ind = 1;

UPDATE HSABC_DEV.ComplianceSourceFileConfig
SET Active_Ind = 1, LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND File_Vrsn_Cd = '{{NEW_FILE_VRSN}}' AND Active_Ind = 0;

-- 2.2 Query sources: the new staging / core tables and the query on the new source tables
UPDATE HSABC_DEV.ComplianceInternalSourceConfig
SET Stg_Table_Nm = OREPLACE(Stg_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}'),
    Core_Table_Nm = OREPLACE(Core_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}'),
    Src_Sql_Txt = OREPLACE(Src_Sql_Txt, '{{OLD_TAG}}', '{{NEW_TAG}}'),
    LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Active_Ind = 1
  AND (Stg_Table_Nm LIKE '%{{OLD_TAG}}%' OR Core_Table_Nm LIKE '%{{OLD_TAG}}%' OR Src_Sql_Txt LIKE '%{{OLD_TAG}}%');

-- 2.2b Only if the columns of a source's query change in the new protocol (one statement per source)
-- UPDATE HSABC_DEV.ComplianceInternalSourceConfig
-- SET Src_Sql_Txt = '{{NEW_QUERY}}', LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
-- WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm = '{{TABLE}}' AND Src_ID = '{{SRC_ID}}';

-- 2.3 Export: the new export table (and the table names among the export procedure's parameters)
UPDATE HSABC_DEV.ComplianceExportConfig
SET Exp_Table_Nm = OREPLACE(Exp_Table_Nm, '{{OLD_TAG}}', '{{NEW_TAG}}'),
    Exp_Proc_Parm_Txt = OREPLACE(Exp_Proc_Parm_Txt, '{{OLD_TAG}}', '{{NEW_TAG}}'),
    LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Exp_Table_Nm LIKE '%{{OLD_TAG}}%';

-- 2.4 Only if the file layout changes: the checks of the table (one statement per check)
-- UPDATE HSABC_DEV.ComplianceFileCheckConfig
-- SET Check_Val_Txt = '{{NEW_VALUE}}', LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
-- WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm = '{{TABLE}}' AND Src_ID = '*' AND Check_Cd = 'HEADER_NAMES';

-- 2.5 Optional tidy-up: the old table-named columns off (they no longer match any active table)
-- UPDATE HSABC_DEV.ComplianceTargetColumnConfig
-- SET Active_Ind = 0, LastUpdt_Dtts = CURRENT_TIMESTAMP(6), LastUpdt_ID = USER
-- WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Column_Nm LIKE '%{{OLD_TAG}}%';

-- ---------------------------------------------------------------------------------------------------------------
-- Part 3: check after Part 2

SELECT 'FILE' AS Cfg, Table_Nm, Src_ID, File_Vrsn_Cd AS Vrsn, Stg_Table_Nm, Core_Table_Nm, Active_Ind
FROM HSABC_DEV.ComplianceSourceFileConfig
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}})
UNION ALL
SELECT 'QUERY', Table_Nm, Src_ID, NULL, Stg_Table_Nm, Core_Table_Nm, Active_Ind
FROM HSABC_DEV.ComplianceInternalSourceConfig
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}})
UNION ALL
SELECT 'EXPORT', Table_Nm, NULL, NULL, NULL, Exp_Table_Nm, Active_Ind
FROM HSABC_DEV.ComplianceExportConfig
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}})
ORDER BY 2, 1, 3, 4;

SELECT Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Effective_Start_Dt_Key, Effective_End_Dt_Key
FROM HSABC_DEV.ComplianceDataSetSourceXwalk
WHERE Project_Cd = '{{PROJECT}}' AND Table_Nm IN ({{TABLES}}) AND Active_Ind = 1
ORDER BY Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key;
