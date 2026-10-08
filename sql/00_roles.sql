-- Runs once when the database volume is first created.
-- The query engine connects as app_ro: read-only, with a statement timeout.
CREATE ROLE app_ro LOGIN PASSWORD 'app_ro';
GRANT CONNECT ON DATABASE appdb TO app_ro;
GRANT USAGE ON SCHEMA public TO app_ro;
ALTER ROLE app_ro SET default_transaction_read_only = on;
ALTER ROLE app_ro SET statement_timeout = '5s';
-- tables created later by user "app" become readable by app_ro automatically
ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA public GRANT SELECT ON TABLES TO app_ro;
