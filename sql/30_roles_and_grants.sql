-- Database roles used by the backend (idempotent; re-run after every load).
--   app_ro : read-only, used for every read endpoint and the metric query engine
--   app_rw : used ONLY for write endpoints; may read everything but change only ops_* tables.
--            Olist data (raw_*), derived (core_*, fact_*) and syn_* tables are physically
--            unwritable by the API, whatever the agent asks for.
-- Passwords are development defaults for the local docker database; override in .env if exposed.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_ro') THEN
    CREATE ROLE app_ro LOGIN PASSWORD 'app_ro';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_rw') THEN
    CREATE ROLE app_rw LOGIN PASSWORD 'app_rw';
  END IF;
END $$;

GRANT CONNECT ON DATABASE appdb TO app_ro, app_rw;
GRANT USAGE ON SCHEMA public TO app_ro, app_rw;
ALTER ROLE app_ro SET default_transaction_read_only = on;
ALTER ROLE app_ro SET statement_timeout = '5s';
ALTER ROLE app_rw SET statement_timeout = '5s';

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ro, app_rw;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO app_ro, app_rw;

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'ops\_%' LOOP
    EXECUTE format('GRANT INSERT, UPDATE, DELETE ON %I TO app_rw', t);
  END LOOP;
  FOR t IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' AND sequencename LIKE 'ops\_%' LOOP
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I TO app_rw', t);
  END LOOP;
END $$;
-- the audit log is append-only for the API (undo marks rows, never deletes them)
DO $$ BEGIN
  IF to_regclass('public.ops_action_log') IS NOT NULL THEN
    REVOKE DELETE ON ops_action_log FROM app_rw;
  END IF;
END $$;
