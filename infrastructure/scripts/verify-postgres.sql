\set ON_ERROR_STOP on
SELECT 1 / CASE WHEN (
    SELECT count(*) = 3
    FROM pg_catalog.pgaadauth_list_principals(false) AS principal
    JOIN pg_roles AS role ON role.rolname = principal.rolename
    WHERE ((principal.rolename = 'accelerator_api' AND principal."objectId" = :'api_principal_id')
        OR (principal.rolename = 'accelerator_worker' AND principal."objectId" = :'worker_principal_id')
        OR (principal.rolename = 'accelerator_migrator' AND principal."objectId" = :'migrator_principal_id'))
      AND principal."principalType" = 'service' AND principal."isAdmin" = 0
      AND NOT role.rolsuper AND NOT role.rolcreatedb AND NOT role.rolcreaterole
      AND has_database_privilege(role.rolname, :'database_name', 'CONNECT')
) THEN 1 ELSE 0 END AS workload_principals_verified;
