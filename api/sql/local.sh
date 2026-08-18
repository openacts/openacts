#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=api_user="$OPENACTS_API_USER" \
    --set=api_password="$OPENACTS_API_PASSWORD" \
    --set=test_database="$OPENACTS_TEST_DATABASE" \
    --set=projection_user="$POSTGRES_USER" <<'SQL'
CREATE ROLE :"api_user" LOGIN PASSWORD :'api_password';
ALTER ROLE :"api_user" SET default_transaction_read_only = on;
CREATE DATABASE :"test_database" OWNER :"projection_user";
SQL

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$OPENACTS_TEST_DATABASE" \
    --file /docker-entrypoint-initdb.d/001_projection.sql

configure_reader() {
    psql -v ON_ERROR_STOP=1 \
        --username "$POSTGRES_USER" \
        --dbname "$1" \
        --set=database="$1" \
        --set=api_user="$OPENACTS_API_USER" \
        --set=projection_user="$POSTGRES_USER" <<'SQL'
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO :"api_user";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"api_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"api_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"projection_user" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"api_user";
SQL
}

configure_reader "$POSTGRES_DB"
configure_reader "$OPENACTS_TEST_DATABASE"

