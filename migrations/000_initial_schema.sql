-- Esquema base (tablas users + catalogos). Ejecutar en una base de datos vacía
-- antes de 001–004, o usar solo pg_dump desde tu PC si quieres una copia idéntica.
--
--   createdb -U postgres nags_search   # o el nombre que uses
--   psql ... -f migrations/000_initial_schema.sql
--   psql ... -f migrations/001_stripe_columns.sql
--   psql ... -f migrations/002_search_extensions_indexes.sql
--   psql ... -f migrations/003_password_reset.sql
--   psql ... -f migrations/004_sessions_and_plan.sql

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255),
    subscription_status VARCHAR(50) NOT NULL DEFAULT 'pending_payment',
    subscription_plan VARCHAR(20),
    stripe_customer_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255),
    subscription_current_period_end TIMESTAMPTZ,
    password_reset_token_hash VARCHAR(255),
    password_reset_expires_at TIMESTAMP,
    password_reset_used_at TIMESTAMP,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    CONSTRAINT users_username_unique UNIQUE (username),
    CONSTRAINT users_email_unique UNIQUE (email),
    CONSTRAINT users_role_check CHECK (role IN ('user', 'admin'))
);

CREATE TABLE IF NOT EXISTS catalogos (
    id BIGSERIAL PRIMARY KEY,
    catalogo_nombre TEXT NOT NULL,
    pagina INTEGER NOT NULL,
    texto TEXT,
    pdf_path TEXT
);
