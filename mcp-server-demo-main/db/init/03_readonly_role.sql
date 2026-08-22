CREATE ROLE factory_readonly LOGIN PASSWORD 'factory_readonly';
GRANT CONNECT ON DATABASE factory TO factory_readonly;
GRANT USAGE ON SCHEMA public TO factory_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO factory_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO factory_readonly;
