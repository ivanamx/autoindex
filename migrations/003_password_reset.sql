-- Restablecimiento de contraseña (tokens de un solo uso con expiración).
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token_hash VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_used_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_users_password_reset_hash ON users (password_reset_token_hash);
