#!/bin/bash
set -e
export PGPASSWORD=$(cat /run/secrets/postgres_password)

# 创建mlflow用户和数据库
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'mlflow') THEN
            CREATE USER mlflow WITH PASSWORD '$(cat /run/secrets/postgres_password)';
        END IF;
    END
    \$\$;

    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow_db') THEN
            CREATE DATABASE mlflow_db OWNER mlflow;
        END IF;
    END
    \$\$;

    -- 核心：授予mlflow用户mlflow_db的所有权限
    \c mlflow_db
    CREATE EXTENSION IF NOT EXISTS vector;
    GRANT ALL PRIVILEGES ON SCHEMA public TO mlflow;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO mlflow;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO mlflow;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO mlflow;
EOSQL

echo "✅ MLflow用户、数据库、权限配置完成"
