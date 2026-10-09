\set ON_ERROR_STOP on
-- Only the migrator may create objects; it owns the tables and grants the runtime
-- roles their table privileges after each upgrade (accelerator.migrations.grants).
GRANT USAGE ON SCHEMA public TO accelerator_api, accelerator_worker, accelerator_migrator;
GRANT CREATE ON SCHEMA public TO accelerator_migrator;
REVOKE CREATE ON SCHEMA public FROM accelerator_api, accelerator_worker;
