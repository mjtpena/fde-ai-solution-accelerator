BEGIN;

CREATE TABLE audit_event (
    event_id uuid PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    event_type varchar(32) NOT NULL,
    outcome varchar(16) NOT NULL,
    correlation_id varchar(128) NOT NULL CHECK (length(correlation_id) > 0),
    actor_id varchar(128) CHECK (length(actor_id) > 0),
    approval_id uuid,
    tool_name varchar(128),
    CONSTRAINT audit_event_valid_fields CHECK (
        (event_type = 'auth_failure' AND outcome = 'failed'
            AND actor_id IS NULL AND approval_id IS NULL AND tool_name IS NULL)
        OR
        (event_type = 'approval' AND outcome IN ('approved', 'denied')
            AND actor_id IS NOT NULL AND approval_id IS NOT NULL AND tool_name IS NULL)
        OR
        (event_type = 'tool_execution' AND outcome IN ('succeeded', 'failed')
            AND actor_id IS NOT NULL AND approval_id IS NULL
            AND tool_name IS NOT NULL
            AND tool_name ~ '^[A-Za-z0-9_.-]{1,128}$')
    )
);

CREATE INDEX ix_audit_event_order ON audit_event (occurred_at DESC, event_id DESC);
CREATE INDEX ix_audit_event_type_order
    ON audit_event (event_type, occurred_at DESC, event_id DESC);

CREATE FUNCTION reject_audit_event_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only' USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER audit_event_append_only
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation();

CREATE TRIGGER audit_event_no_truncate
    BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation();

COMMIT;
