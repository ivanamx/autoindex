-- Rol de cuenta: user (por defecto) o admin (panel de estadísticas / gestión).
-- Ejecutar una sola vez: psql ... -f migrations/008_user_role.sql

ALTER TABLE users
    ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user';

ALTER TABLE users
    ADD CONSTRAINT users_role_check CHECK (role IN ('user', 'admin'));

COMMENT ON COLUMN users.role IS 'user = cuenta normal; admin = acceso a rutas de administración';
