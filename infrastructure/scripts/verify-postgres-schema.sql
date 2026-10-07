\set ON_ERROR_STOP on
SELECT 1 / CASE WHEN
    has_schema_privilege('accelerator_api', 'public', 'USAGE')
    AND has_schema_privilege('accelerator_worker', 'public', 'USAGE')
THEN 1 ELSE 0 END AS workload_schema_access_verified;
