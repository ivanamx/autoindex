Lo que debes hacer en el Dashboard de Stripe (obligatorio para que “se vea todo”)
El código solo abre el portal; qué opciones aparecen lo defines tú en Stripe:

Entra en Configuración → Portal de facturación del cliente (Customer / Billing portal).
Ruta típica: Stripe Dashboard → Settings → Billing → Customer portal.

Activa al menos:

Ver historial de facturas (y descarga).
Actualizar método de pago.
Cancelar suscripción (recomendado: al final del periodo de facturación).
Reactivar: en el mismo sitio de configuración del portal, habilita que los clientes puedan reanudar / quitar la cancelación programada si Stripe lo ofrece en tu modo de suscripción (suele aparecer como opción relacionada con cancelaciones).

Cambiar de plan: en la sección de productos/precios del portal, añade los precios entre los que quieres permitir cambio (ej. mensual ↔ anual). Si no configuras precios “elegibles”, el usuario no verá cambio de plan.

En modo test repite lo mismo en el entorno de pruebas.