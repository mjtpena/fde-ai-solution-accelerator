\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.ensure_workload_principal(role_name text, object_id text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pgaadauth_list_principals(false)
            WHERE rolename = role_name AND "objectId" = object_id
              AND "principalType" = 'service' AND "isAdmin" = 0
        ) THEN
            RAISE EXCEPTION 'Existing role % does not match the intended nonadmin Entra principal', role_name;
        END IF;
    ELSE
        PERFORM pg_catalog.pgaadauth_create_principal_with_oid(role_name, object_id, 'service', false, false);
    END IF;
END;
$$;

SELECT pg_temp.ensure_workload_principal('accelerator_api', :'api_principal_id');
SELECT pg_temp.ensure_workload_principal('accelerator_worker', :'worker_principal_id');
SELECT pg_temp.ensure_workload_principal('accelerator_migrator', :'migrator_principal_id');
SELECT format(
    'GRANT CONNECT ON DATABASE %I TO accelerator_api, accelerator_worker, accelerator_migrator',
    :'database_name'
) \gexec
COMMIT;
