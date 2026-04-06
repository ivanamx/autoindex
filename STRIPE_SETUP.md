# Configuración de Stripe y entorno (NAGS)

Este documento resume cómo está integrado el pago en el código, qué variables debes tener en `.env`, y la checklist en el **Dashboard de Stripe** para producción. Úsalo junto con el aviso de privacidad del sitio (tratamiento de datos con Stripe).

---

## 1. Cómo funciona el flujo en la aplicación

1. **Registro + Checkout:** `POST /api/register-checkout` crea un usuario con `subscription_status = pending_payment`, abre una **Stripe Checkout Session** en modo `subscription` y devuelve la URL de pago.
2. **Éxito:** el usuario vuel a `GET /subscription/success` (página informativa). El estado **activo** y los datos de facturación se confirman por **webhooks**.
3. **Webhooks:** `POST /webhooks/stripe` verifica la firma con `STRIPE_WEBHOOK_SECRET` y procesa:
   - `checkout.session.completed` → guarda `stripe_customer_id`, `stripe_subscription_id`, pone la cuenta en **activa** y el plan según metadata.
   - `customer.subscription.updated` → actualiza estado, plan (según *Price ID*), y `subscription_current_period_end`.
   - `customer.subscription.deleted` → marca la suscripción como cancelada en la base de datos.
4. **Portal de facturación:** desde el panel, `POST /dashboard/stripe-portal` abre el **Customer Billing Portal** de Stripe (página alojada por Stripe) para método de pago, facturas y cancelación según lo que habilites en Stripe.

No se usa Stripe.js ni clave publicable en el front: el cobro ocurre en la página hospedada por Stripe (Checkout + Portal).

---

## 2. Variables de entorno (`.env`)

### Imprescindibles para pagos

| Variable | Ejemplo / formato | Uso |
|----------|-------------------|-----|
| `STRIPE_SECRET_KEY` | `sk_test_...` / `sk_live_...` | API secreta de Stripe (servidor). |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | Firma de los webhooks del endpoint que configures (cada endpoint tiene su secreto). |
| `STRIPE_PRICE_MONTHLY` | `price_...` | ID del precio **recurrente mensual** en Stripe. |
| `STRIPE_PRICE_ANNUAL` | `price_...` | ID del precio **recurrente anual** en Stripe. |

### Muy recomendada en producción

| Variable | Uso |
|----------|-----|
| `PUBLIC_BASE_URL` | URL pública **sin barra final**, p. ej. `https://tudominio.com`. Sin esto, en algunos despliegues las URLs de éxito/cancelación de Checkout y la `return_url` del portal pueden construirse mal. |

### Resto del proyecto (no son de Stripe pero el sitio las usa)

- `SECRET_KEY`, `DB_CONNECTION_STRING`
- Migraciones SQL: `migrations/001_stripe_columns.sql`, `migrations/004_sessions_and_plan.sql` (columnas `subscription_plan`, `subscription_current_period_end`, sesiones por dispositivo)
- Email / soporte: `LEGAL_CONTACT_EMAIL`, `WHATSAPP_PHONE` (opcional), SMTP si aplica

**No** hace falta `STRIPE_PUBLISHABLE_KEY` para el flujo actual (Checkout por redirección).

---

## 3. Qué crear en Stripe (Productos y precios)

1. Crea un **Producto** (o dos) para tu suscripción NAGS.
2. Añade **dos precios recurrentes** (modo **suscripción**):
   - uno **mensual**;
   - uno **anual**.
3. Copia cada **Price ID** (`price_...`) a `STRIPE_PRICE_MONTHLY` y `STRIPE_PRICE_ANNUAL` en `.env`.
4. El código identifica el plan comparando esos IDs con el ítem de la suscripción en los webhooks; si no coinciden con el precio real del cliente, en el panel puede mostrarse el plan como «—».

---

## 4. Webhook en el Dashboard de Stripe

1. **Developers → Webhooks → Add endpoint**
2. **URL:** `https://TU_DOMINIO/webhooks/stripe` (en local: ver sección 6).
3. **Eventos** a enviar (mínimo los que procesa el código):
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
4. Tras crear el endpoint, copia el **Signing secret** (`whsec_...`) a `STRIPE_WEBHOOK_SECRET` en `.env`.
5. En **test** y **live** los secretos y las claves son distintos: usa el entorno correcto en cada despliegue.

---

## 5. Portal de facturación del cliente (Billing portal)

El enlace «Gestionar facturación» solo funciona bien si el portal está configurado en Stripe.

1. **Settings → Billing → Customer portal** (ruta puede variar ligeramente según la interfaz).
2. Activa al menos:
   - Ver historial de facturas y descarga.
   - Actualizar método de pago.
   - Cancelar suscripción (recomendado: **al final del periodo** de facturación).
3. **Cambio de plan:** en la configuración del portal, añade los precios entre los que permites cambiar (p. ej. mensual ↔ anual). Si no los añades, el usuario no verá la opción de cambiar de plan.
4. **Reactivación:** habilita en el portal las opciones que Stripe ofrezca para reactivar o anular una cancelación programada.

Repite la configuración en **modo test** si desarrollas con claves de prueba.

---

## 6. Pruebas locales

1. Instala [Stripe CLI](https://stripe.com/docs/stripe-cli).
2. Autenticación: `stripe login`
3. Reenvío de webhooks, por ejemplo:

   ```bash
   stripe listen --forward-to localhost:5001/webhooks/stripe
   ```

4. La CLI mostrará un **webhook signing secret** temporal (`whsec_...`): úsalo en `.env` como `STRIPE_WEBHOOK_SECRET` **solo para esa sesión de prueba**, o crea un endpoint de test en el Dashboard que apunte a un túnel (ngrok, etc.).

5. Tarjetas de prueba: [documentación Stripe Testing](https://stripe.com/docs/testing).

---

## 7. Checklist antes de pasar a producción

- [ ] `STRIPE_SECRET_KEY` es **live** (`sk_live_...`).
- [ ] Precios **live** `price_...` en `STRIPE_PRICE_MONTHLY` y `STRIPE_PRICE_ANNUAL`.
- [ ] Endpoint webhook **live** con los tres eventos indicados y `STRIPE_WEBHOOK_SECRET` **live** correspondiente.
- [ ] `PUBLIC_BASE_URL` apunta al dominio real con HTTPS.
- [ ] Portal de facturación configurado y probado con un cliente de prueba en live (opcional pero recomendable).
- [ ] Migraciones SQL aplicadas en la base de producción.
- [ ] Firewall / proxy permite `POST` sin body transformado hacia `/webhooks/stripe` (Stripe necesita el cuerpo crudo para validar la firma).

---

## 8. Coherencia con privacidad

El aviso de privacidad del sitio describe el tratamiento de datos con **Stripe** (pagos, identificadores de cliente/suscripción, sincronización de estado y periodo de facturación, portal de autoservicio y metadatos para vincular el pago con la cuenta). Mantén actualizado el correo de contacto (`LEGAL_CONTACT_EMAIL` / `site_contact_email` en plantillas) para derechos ARCO y soporte.

---

## 9. Problemas frecuentes

| Síntoma | Revisar |
|---------|---------|
| Panel muestra plan o fin de periodo «—» | Webhooks no llegan, o `STRIPE_PRICE_*` no coinciden con el precio real de la suscripción. |
| `stripe_customer_id` nulo tras pagar | `checkout.session.completed` no se procesó (webhook o secreto incorrecto). |
| Portal de facturación da error | Portal no activado en Stripe o `stripe_customer_id` no guardado. |
| Redirección extraña tras pagar | `PUBLIC_BASE_URL` vacío o incorrecto. |
