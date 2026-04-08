-- El CHECK antiguo solo permitía active/inactive/expired; la app (Stripe) usa
-- pending_payment, canceled, past_due, etc. Sin esto, INSERT en registro/checkout falla.

ALTER TABLE users DROP CONSTRAINT IF EXISTS valid_subscription;

ALTER TABLE users ADD CONSTRAINT valid_subscription CHECK (
    subscription_status = ANY (ARRAY[
        'active'::text,
        'inactive'::text,
        'expired'::text,
        'pending_payment'::text,
        'past_due'::text,
        'canceled'::text
    ])
);
