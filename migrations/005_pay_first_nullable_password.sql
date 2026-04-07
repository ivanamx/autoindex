-- Cuentas creadas tras pago (pay-first): contraseña se define con el enlace enviado por correo.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
