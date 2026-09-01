-- Read-only role for the analytics MCP server. Never grant write/DDL here.
CREATE ROLE olist_readonly LOGIN PASSWORD 'olist_readonly';
GRANT CONNECT ON DATABASE olist TO olist_readonly;
GRANT USAGE ON SCHEMA public TO olist_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO olist_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO olist_readonly;
