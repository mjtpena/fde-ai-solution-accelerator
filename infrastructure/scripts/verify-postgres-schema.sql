\set ON_ERROR_STOP on
SELECT 1 / CASE WHEN
    has_schema_privilege('accelerator_api', 'public', 'USAGE')
    AND has_schema_privilege('accelerator_worker', 'public', 'USAGE')
    AND has_schema_privilege('accelerator_migrator', 'public', 'USAGE')
    AND has_schema_privilege('accelerator_migrator', 'public', 'CREATE')
    AND NOT has_schema_privilege('accelerator_api', 'public', 'CREATE')
    AND NOT has_schema_privilege('accelerator_worker', 'public', 'CREATE')
THEN 1 ELSE 0 END AS workload_schema_access_verified;
