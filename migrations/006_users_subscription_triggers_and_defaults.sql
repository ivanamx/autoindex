-- Si /api/register-checkout sigue fallando con valid_subscription aunque la app
-- inserte plan y periodo NULL, suele haber DEFAULTs o un TRIGGER BEFORE INSERT
-- (muy común en dumps de desarrollo) que fuerzan subscription_plan = 'free' y
-- rellenan subscription_current_period_end.

-- Quitar defaults que no deben aplicarse a usuarios nuevos en checkout
ALTER TABLE users ALTER COLUMN subscription_current_period_end DROP DEFAULT;

-- Por si 005 no se llegó a aplicar:
ALTER TABLE users ALTER COLUMN subscription_plan DROP DEFAULT;
ALTER TABLE users ALTER COLUMN subscription_plan SET DEFAULT NULL;

-- Listar triggers personalizados en public.users (ejecutar en psql y revisar salida):
-- SELECT tgname, pg_get_triggerdef(oid)
-- FROM pg_trigger
-- WHERE tgrelid = 'public.users'::regclass AND NOT tgisinternal;

-- Si aparece uno que asigna plan gratuito o fechas al insertar, elimínalo, por ejemplo:
-- DROP TRIGGER IF EXISTS nombre_exacto_del_paso_anterior ON public.users;
