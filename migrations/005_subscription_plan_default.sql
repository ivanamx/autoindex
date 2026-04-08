-- Algunas BDs locales tenían DEFAULT 'free' en subscription_plan; al registrar
-- con subscription_status = pending_payment eso viola el CHECK valid_subscription.
-- Deja el plan en NULL hasta que Stripe/webhooks fijen monthly/annual.

ALTER TABLE users
    ALTER COLUMN subscription_plan SET DEFAULT NULL;
