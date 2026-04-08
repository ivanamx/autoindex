from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file, session
import os
import re
import hashlib
import secrets
import smtplib
from email.message import EmailMessage
from pathlib import Path
import psycopg2
from psycopg2 import errors as pg_errors
import stripe
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
from datetime import datetime, timezone
from typing import Optional
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.getenv('SECRET_KEY', 'supersecretkey')
app.config['REMEMBER_COOKIE_DURATION'] = 604800  # 7 días

bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@app.context_processor
def inject_legal_context():
    """Variables compartidas en plantillas legales y de pie de página."""
    email = (os.getenv('LEGAL_CONTACT_EMAIL') or 'contacto@tudominio.com').strip()
    wa_raw = (os.getenv('WHATSAPP_PHONE') or '').strip()
    wa_digits = re.sub(r'\D', '', wa_raw) if wa_raw else ''
    return {
        'site_name': 'AutoIndex',
        'site_contact_email': email,
        'whatsapp_phone': wa_digits,
        'legal_last_updated_iso': '2026-04-07',
        'legal_last_updated_human': '7 de abril de 2026',
    }


MAX_DEVICE_SESSIONS_MONTHLY = 3
MAX_DEVICE_SESSIONS_ANNUAL = 5
MAX_DEVICE_SESSIONS_NON_ACTIVE = 3
FREE_DAILY_SEARCH_LIMIT = 3


# === USER MODEL ===
class User(UserMixin):
    def __init__(self, id, username, email, password_hash, subscription_status, subscription_plan=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.subscription_status = subscription_status
        self.subscription_plan = subscription_plan


@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, username, email, password_hash, subscription_status, subscription_plan
        FROM users WHERE id = %s
        """,
        (user_id,),
    )
    user = cur.fetchone()

    cur.close()
    conn.close()

    if user:
        return User(*user)
    return None

PDF_DIR = (os.getenv("PDF_DIR") or r"C:\Users\ivanam.PSSJotace\nags\pdfs").strip()

# Búsqueda pública (sin login o sin suscripción activa): debe coincidir con
# catalogo_nombre en BD (el indexador usa el nombre del PDF, ej. "NAGS 2025.pdf").
PUBLIC_CATALOGO_NOMBRE = "NAGS 2025.pdf"


def has_full_catalog_access():
    return (
        current_user.is_authenticated
        and getattr(current_user, "subscription_status", None) == "active"
    )


def _public_daily_search_stats() -> tuple[int, int]:
    """Devuelve (consumidas_hoy, restantes_hoy) para modo gratis por sesión."""
    today = datetime.now().strftime("%Y-%m-%d")
    usage = session.get("public_search_usage")

    if not isinstance(usage, dict) or usage.get("day") != today:
        usage = {"day": today, "count": 0}
        session["public_search_usage"] = usage

    raw_count = usage.get("count", 0)
    count = raw_count if isinstance(raw_count, int) and raw_count >= 0 else 0
    remaining = max(0, FREE_DAILY_SEARCH_LIMIT - count)
    return count, remaining


def _consume_public_daily_search() -> tuple[int, int]:
    """Incrementa consumo diario en modo gratis y devuelve (consumidas, restantes)."""
    count, _ = _public_daily_search_stats()
    count += 1
    session["public_search_usage"] = {
        "day": datetime.now().strftime("%Y-%m-%d"),
        "count": count,
    }
    session.modified = True
    return count, max(0, FREE_DAILY_SEARCH_LIMIT - count)


def _stored_pdf_basename(stored_path: Optional[str]) -> str:
    """Nombre del .pdf aunque en BD venga una ruta Windows con backslashes."""
    if not stored_path:
        return ""
    norm = stored_path.replace("\\", "/").strip()
    base = os.path.basename(norm)
    return base or norm


def _resolve_pdf_path(filename: str):
    if not filename:
        return None
    norm = filename.replace("\\", "/").strip()
    candidates = []
    base = os.path.basename(norm)
    if base and base not in (".", ".."):
        candidates.append(base)
    # Rutas corruptas en la URL (p. ej. mezcla de caracteres sin separadores)
    for m in re.finditer(r"[^/\s]+\.pdf\b", norm, flags=re.IGNORECASE):
        candidates.append(m.group(0))
    pdf_dir_real = os.path.realpath(PDF_DIR)
    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if cand in (".", ".."):
            continue
        full = os.path.realpath(os.path.join(PDF_DIR, cand))
        if not full.startswith(pdf_dir_real + os.sep):
            continue
        if not os.path.isfile(full):
            continue
        return cand, full
    return None


def _user_may_read_pdf(basename: str) -> bool:
    if has_full_catalog_access():
        return True
    return basename == PUBLIC_CATALOGO_NOMBRE

def find_pdf_by_year(year):
    """Encuentra el PDF correspondiente al año"""
    pdf_files = list(Path(PDF_DIR).glob("*.pdf"))
    
    for pdf_file in pdf_files:
        filename = pdf_file.name.lower()
        if any(pattern.lower() in filename for pattern in YEAR_PATTERNS.get(year, [])):
            return str(pdf_file)
    
    return None

STOPWORDS = [
    'buscar', 'del', 'de', 'la', 'el', 'los', 'las', 'un', 'una', 'año', 'anio',
    'por', 'favor', 'para', 'con', 'sin', 'sobre', 'entre', 'hasta', 'desde',
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of',
    'with', 'by', 'from', 'up', 'about', 'into', 'through', 'during', 'please',
    'search', 'find', 'look', 'get', 'show', 'me', 'my', 'i', 'you', 'he', 'she'
]

STOPWORDS_SET = set(STOPWORDS)


def _extract_terms_without_year(query_text: str):
    tokens = re.findall(r"[a-zA-Z0-9]+", query_text.lower())
    cleaned = []
    for token in tokens:
        if re.fullmatch(r"(19\d{2}|20\d{2})", token):
            continue
        if len(token) <= 1:
            continue
        if token in STOPWORDS_SET:
            continue
        cleaned.append(token)
    compact = _compact_token(query_text)
    if len(compact) >= 3 and compact not in cleaned and compact not in STOPWORDS_SET:
        cleaned.append(compact)
    return cleaned


def _compact_token(token: str):
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def parse_search_query(query):
    query_lower = query.lower().strip()

    print("DEBUG RAW QUERY:", query_lower)

    # 🔥 EXTRAER CUALQUIER AÑO DE 4 DÍGITOS
    year_match = re.search(r'(19\d{2}|20\d{2})', query_lower)

    if not year_match:
        return {
            'error': 'Año obligatorio (ej: 2018)',
            'year': None,
            'marca': None,
            'modelo': None
        }

    year = int(year_match.group())

    
    search_text = query_lower.replace(year_match.group(), " ").strip()
    words = _extract_terms_without_year(search_text)

    marca = words[0] if words else None
    modelo = " ".join(words[1:]) if len(words) > 1 else None

    print("DEBUG PARSED:", year, marca, modelo)

    return {
        'error': None,
        'year': year,
        'marca': marca,
        'modelo': modelo,
        'terms': words
    }

def get_db_connection():
    return psycopg2.connect(os.getenv("DB_CONNECTION_STRING"))

def _hash_reset_token(token: str) -> str:
    # SHA-256 del token (no se guarda el token en claro)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Envía correo vía SMTP si está configurado.
    Variables de entorno:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
    """
    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int((os.getenv("SMTP_PORT") or "587").strip())
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASS") or "").strip()
    from_email = (os.getenv("SMTP_FROM") or user).strip()

    if not host or not from_email:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)

    return True


def _absolute_base_url():
    fixed = (os.getenv('PUBLIC_BASE_URL') or '').strip().rstrip('/')
    if fixed:
        return fixed
    return request.host_url.rstrip('/')


def _password_reset_url(token: str) -> str:
    base = (os.getenv('PUBLIC_BASE_URL') or '').strip().rstrip('/')
    if base:
        return base + url_for('reset_password', token=token)
    return url_for('reset_password', token=token, _external=True)


def _unique_username_from_email(cur, email: str) -> str:
    local = (email.split('@')[0] if '@' in email else email).lower()
    base = re.sub(r'[^a-z0-9_-]', '', local)
    if len(base) < 2:
        base = 'usuario'
    base = base[:50]
    candidate = base
    n = 0
    while True:
        cur.execute('SELECT 1 FROM users WHERE username = %s', (candidate,))
        if cur.fetchone() is None:
            return candidate
        n += 1
        suffix = f'_{n}'
        max_base = 50 - len(suffix)
        candidate = (base[:max_base] if max_base > 0 else 'u') + suffix


def _stripe_get(obj: object, key: str, default=None):
    """Lee clave en dict o StripeObject (Stripe Python v11+ no tiene .get())."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(key, default)
        return default if v is None else v
    try:
        v = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if v is None else v


def _stripe_resource_id(value: object) -> Optional[str]:
    """Convierte cus_/sub_ a string; si expand devolvió un objeto Stripe, usa su .id."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    if isinstance(value, dict):
        return _stripe_resource_id(value.get('id'))
    rid = getattr(value, 'id', None)
    if isinstance(rid, str) and rid.strip():
        return rid.strip()
    return None


def _stripe_meta_dict(obj: object) -> dict:
    raw = _stripe_get(obj, 'metadata')
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    to_dict = getattr(raw, 'to_dict', None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    out = {}
    try:
        keys = getattr(raw, 'keys', None)
        if callable(keys):
            for k in keys():
                if not isinstance(k, str):
                    continue
                try:
                    out[k] = raw[k]
                except (KeyError, TypeError):
                    pass
    except Exception:
        pass
    return out


def _checkout_email_from_session(sess: object) -> str:
    details = _stripe_get(sess, 'customer_details') or {}
    if isinstance(details, dict):
        raw_email = details.get('email')
    else:
        raw_email = _stripe_get(details, 'email')
    raw = raw_email or _stripe_get(sess, 'customer_email') or ''
    email = (raw or '').strip().lower()
    if email:
        return email
    cust_id = _stripe_resource_id(_stripe_get(sess, 'customer'))
    if not cust_id:
        return ''
    try:
        cust = stripe.Customer.retrieve(cust_id)
        return (_stripe_get(cust, 'email') or '').strip().lower()
    except stripe.StripeError as e:
        print('pay_first: Stripe customer retrieve:', e)
        return ''


def _pay_first_sync_user_from_session(session_obj: object) -> Optional[int]:
    """
    Crea o actualiza el usuario tras checkout pay_first (idempotente).
    Usuario y contraseña definitivos se definen en /completar-cuenta.
    """
    meta = _stripe_meta_dict(session_obj)
    if (meta.get('flow') or '').strip().lower() != 'pay_first':
        return None
    plan_raw = (meta.get('plan') or '').strip().lower()
    plan = plan_raw if plan_raw in ('monthly', 'annual') else 'monthly'
    customer_id = _stripe_resource_id(_stripe_get(session_obj, 'customer'))
    subscription_id = _stripe_resource_id(_stripe_get(session_obj, 'subscription'))
    email = _checkout_email_from_session(session_obj)
    if not email and _stripe_get(session_obj, 'id'):
        try:
            expanded = stripe.checkout.Session.retrieve(
                _stripe_get(session_obj, 'id'),
                expand=['customer'],
            )
            email = _checkout_email_from_session(expanded)
        except stripe.StripeError as e:
            print('pay_first: no se pudo recuperar la sesión', e)
    if not email:
        print('pay_first: sin email en sesión de checkout', _stripe_get(session_obj, 'id'))
        return None
    if not subscription_id:
        print('pay_first: sin subscription id', _stripe_get(session_obj, 'id'))
        return None

    conn = get_db_connection()
    cur = conn.cursor()
    uid: Optional[int] = None
    try:
        cur.execute(
            'SELECT id FROM users WHERE stripe_subscription_id = %s',
            (subscription_id,),
        )
        row_sub = cur.fetchone()
        if row_sub:
            uid = row_sub[0]
            cur.execute(
                """
                UPDATE users SET subscription_status = %s,
                    stripe_customer_id = COALESCE(%s, stripe_customer_id),
                    subscription_plan = COALESCE(%s, subscription_plan)
                WHERE id = %s
                """,
                ('active', customer_id, plan, uid),
            )
        else:
            cur.execute(
                'SELECT id FROM users WHERE email = %s',
                (email,),
            )
            row = cur.fetchone()
            if row:
                uid = row[0]
                cur.execute(
                    """
                    UPDATE users SET subscription_status = %s,
                        stripe_customer_id = COALESCE(%s, stripe_customer_id),
                        stripe_subscription_id = %s,
                        subscription_plan = COALESCE(%s, subscription_plan)
                    WHERE id = %s
                    """,
                    ('active', customer_id, subscription_id, plan, uid),
                )
            else:
                provisional_username = _unique_username_from_email(cur, email)
                cur.execute(
                    """
                    INSERT INTO users (
                        username, email, password_hash, subscription_status,
                        stripe_customer_id, stripe_subscription_id, subscription_plan
                    )
                    VALUES (%s, %s, NULL, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (provisional_username, email, 'active', customer_id, subscription_id, plan),
                )
                uid = cur.fetchone()[0]

        conn.commit()
    except pg_errors.UniqueViolation:
        conn.rollback()
        cur.execute(
            'SELECT id FROM users WHERE stripe_subscription_id = %s OR email = %s',
            (subscription_id, email),
        )
        row = cur.fetchone()
        if row:
            uid = row[0]
            cur.execute(
                """
                UPDATE users SET subscription_status = %s,
                    stripe_customer_id = COALESCE(%s, stripe_customer_id),
                    stripe_subscription_id = COALESCE(%s, stripe_subscription_id),
                    subscription_plan = COALESCE(%s, subscription_plan)
                WHERE id = %s
                """,
                ('active', customer_id, subscription_id, plan, uid),
            )
            conn.commit()
        else:
            conn.rollback()
    except Exception as e:
        conn.rollback()
        print('pay_first checkout DB error:', e)
        raise
    finally:
        try:
            if cur and not cur.closed:
                cur.close()
            if conn and not conn.closed:
                conn.close()
        except Exception:
            pass

    if uid is None:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                'SELECT id FROM users WHERE stripe_subscription_id = %s',
                (subscription_id,),
            )
            r3 = cur.fetchone()
            if r3:
                uid = r3[0]
        finally:
            cur.close()
            conn.close()

    if uid is not None:
        _enforce_device_session_cap(uid)
    return uid


def _stripe_pay_first_checkout_completed(session_obj: object):
    """Webhook: sincroniza usuario pay_first (sin correo; completar datos en el sitio)."""
    _pay_first_sync_user_from_session(session_obj)


def _retrieve_pay_first_checkout_session(session_id: str):
    """Sesión Stripe: pay_first + suscripción (aún sin exigir pago confirmado)."""
    if not session_id or not stripe.api_key:
        return None
    try:
        sess = stripe.checkout.Session.retrieve(session_id.strip(), expand=['customer'])
    except stripe.StripeError:
        return None
    meta = _stripe_meta_dict(sess)
    if (meta.get('flow') or '').strip().lower() != 'pay_first':
        return None
    if _stripe_get(sess, 'mode') != 'subscription':
        return None
    return sess


def _checkout_session_payment_ready(sess: object) -> bool:
    ps = _stripe_get(sess, 'payment_status')
    return ps in ('paid', 'no_payment_required')


def _verify_pay_first_checkout_session(session_id: str):
    """Sesión pay_first con pago confirmado en Stripe."""
    sess = _retrieve_pay_first_checkout_session(session_id)
    if not sess or not _checkout_session_payment_ready(sess):
        return None
    return sess


def _price_id_for_plan(plan: str):
    if plan == 'monthly':
        return os.getenv('STRIPE_PRICE_MONTHLY')
    if plan == 'annual':
        return os.getenv('STRIPE_PRICE_ANNUAL')
    return None


def _map_stripe_subscription_status(stripe_status: str) -> str:
    return {
        'active': 'active',
        'trialing': 'active',
        'past_due': 'past_due',
        'unpaid': 'past_due',
        'canceled': 'canceled',
        'incomplete': 'pending_payment',
        'incomplete_expired': 'canceled',
        'paused': 'past_due',
    }.get(stripe_status, 'past_due')


def _max_device_sessions(subscription_plan: Optional[str], subscription_status: Optional[str]) -> int:
    if subscription_status != 'active':
        return MAX_DEVICE_SESSIONS_NON_ACTIVE
    if subscription_plan == 'annual':
        return MAX_DEVICE_SESSIONS_ANNUAL
    return MAX_DEVICE_SESSIONS_MONTHLY


def _plan_label(plan: Optional[str]) -> str:
    if plan == 'annual':
        return 'Anual'
    if plan == 'monthly':
        return 'Mensual'
    return '—'


def _stripe_price_to_plan(price_id: Optional[str]) -> Optional[str]:
    if not price_id:
        return None
    monthly_pid = (os.getenv('STRIPE_PRICE_MONTHLY') or '').strip()
    annual_pid = (os.getenv('STRIPE_PRICE_ANNUAL') or '').strip()
    if annual_pid and price_id == annual_pid:
        return 'annual'
    if monthly_pid and price_id == monthly_pid:
        return 'monthly'
    return None


def _register_device_session(
    user_id: int,
    subscription_plan: Optional[str],
    subscription_status: Optional[str],
    remember_me: bool,
    user_agent: Optional[str],
):
    """Crea sesión de dispositivo y respeta el tope según plan."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_reset_token(token)
    cap = _max_device_sessions(subscription_plan, subscription_status)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        while True:
            cur.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = %s", (user_id,))
            n = cur.fetchone()[0]
            if n < cap:
                break
            cur.execute(
                """
                DELETE FROM user_sessions
                WHERE id = (
                    SELECT id FROM user_sessions WHERE user_id = %s ORDER BY created_at ASC LIMIT 1
                )
                """,
                (user_id,),
            )
        cur.execute(
            """
            INSERT INTO user_sessions (user_id, token_hash, user_agent, remember_me)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, token_hash, (user_agent or '')[:512], remember_me),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    session['device_token'] = token


def _touch_device_session(user_id: int, token_plain: str) -> bool:
    token_hash = _hash_reset_token(token_plain)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE user_sessions
            SET last_seen_at = NOW()
            WHERE user_id = %s AND token_hash = %s
            RETURNING id
            """,
            (user_id, token_hash),
        )
        ok = cur.fetchone() is not None
        conn.commit()
        return ok
    finally:
        cur.close()
        conn.close()


def _revoke_device_session(user_id: int, token_plain: str):
    token_hash = _hash_reset_token(token_plain)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM user_sessions WHERE user_id = %s AND token_hash = %s",
            (user_id, token_hash),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _revoke_other_device_sessions(user_id: int, keep_token_plain: str):
    keep_hash = _hash_reset_token(keep_token_plain)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            DELETE FROM user_sessions
            WHERE user_id = %s AND token_hash != %s
            """,
            (user_id, keep_hash),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _enforce_device_session_cap(user_id: int):
    """Si cambia el plan o estado, elimina sesiones sobrantes (las más antiguas)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT subscription_plan, subscription_status FROM users WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        plan, st = row
        cap = _max_device_sessions(plan, st)
        while True:
            cur.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE user_id = %s",
                (user_id,),
            )
            n = cur.fetchone()[0]
            if n <= cap:
                break
            cur.execute(
                """
                DELETE FROM user_sessions
                WHERE id = (
                    SELECT id FROM user_sessions WHERE user_id = %s ORDER BY created_at ASC LIMIT 1
                )
                """,
                (user_id,),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _bootstrap_legacy_device_session(user_id: int):
    """Usuarios con sesión Flask-login previa al registro de dispositivos."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT subscription_plan, subscription_status FROM users WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        plan, st = row if row else (None, None)
    finally:
        cur.close()
        conn.close()
    _register_device_session(
        user_id,
        plan,
        st,
        remember_me=False,
        user_agent=request.headers.get('User-Agent'),
    )


_SKIP_DEVICE_SESSION_ENDPOINTS = frozenset(
    {
        None,
        'static',
        'login',
        'logout',
        'forgot_password',
        'reset_password',
        'stripe_webhook',
        'health',
        'terminos',
        'privacidad',
        'preguntas_frecuentes',
        'register',
        'api_register_checkout',
        'api_checkout_pay_first_monthly',
        'completar_cuenta',
        'api_completar_cuenta_poll',
        'api_subscription_success_poll',
    }
)


@app.before_request
def _require_registered_device_session():
    if request.endpoint in _SKIP_DEVICE_SESSION_ENDPOINTS:
        return
    if not current_user.is_authenticated:
        return
    token = session.get('device_token')
    if not token:
        try:
            _bootstrap_legacy_device_session(current_user.id)
            token = session.get('device_token')
        except Exception as exc:
            print('bootstrap device session:', exc)
            token = None
        if not token:
            session.pop('device_token', None)
            logout_user()
            flash('No se pudo validar tu sesión. Si acabas de actualizar el sitio, ejecuta la migración 004 y vuelve a entrar.')
            return redirect(url_for('login'))
    if not _touch_device_session(current_user.id, token):
        session.pop('device_token', None)
        logout_user()
        flash('Tu sesión ya no es válida o se cerró desde otro dispositivo.')
        return redirect(url_for('login'))


def _stripe_handle_checkout_completed(session: object):
    meta = _stripe_meta_dict(session)
    if (meta.get('flow') or '').strip().lower() == 'pay_first':
        _stripe_pay_first_checkout_completed(session)
        return
    user_id = meta.get('user_id')
    if not user_id:
        return
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return
    plan_raw = (meta.get('plan') or '').strip().lower()
    plan = plan_raw if plan_raw in ('monthly', 'annual') else None
    customer_id = _stripe_resource_id(_stripe_get(session, 'customer'))
    subscription_id = _stripe_resource_id(_stripe_get(session, 'subscription'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users SET subscription_status = %s,
            stripe_customer_id = %s,
            stripe_subscription_id = %s,
            subscription_plan = COALESCE(%s, subscription_plan)
        WHERE id = %s
        """,
        ('active', customer_id, subscription_id, plan, uid),
    )
    conn.commit()
    cur.close()
    conn.close()
    _enforce_device_session_cap(uid)


def _login_user_from_db_row_after_payment(uid: int):
    """Tras pago verificado: inicia sesión Flask + sesión de dispositivo."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, username, email, password_hash, subscription_status, subscription_plan
        FROM users WHERE id = %s
        """,
        (uid,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row[3]:
        flash('Tu cuenta se está activando. En unos segundos puedes iniciar sesión.')
        return redirect(url_for('login'))
    user_obj = User(*row)
    login_user(user_obj, remember=True)
    try:
        _register_device_session(
            user_obj.id,
            user_obj.subscription_plan,
            user_obj.subscription_status,
            True,
            request.headers.get('User-Agent'),
        )
    except Exception as exc:
        print('autologin device session:', exc)
        session.pop('device_token', None)
        logout_user()
        flash('El pago se registró, pero no pudimos iniciar sesión automáticamente. Entra manualmente.')
        return redirect(url_for('login'))
    flash('¡Bienvenido! Ya tienes acceso completo.')
    return redirect(url_for('index'))


def _try_autologin_from_checkout_session_id(session_id: str):
    """
    Verifica session_id con Stripe, sincroniza suscripción (flujo registro / checkout-monthly)
    y deja al usuario logueado. pay_first → redirige a completar-cuenta.
    Devuelve Response o None si no aplica autologin.
    """
    sid = (session_id or '').strip()
    if not sid or not stripe.api_key:
        return None
    try:
        sess = stripe.checkout.Session.retrieve(sid, expand=['customer'])
    except stripe.StripeError:
        return None

    meta = _stripe_meta_dict(sess)
    if (meta.get('flow') or '').strip().lower() == 'pay_first':
        return redirect(url_for('completar_cuenta', session_id=sid))

    if not _checkout_session_payment_ready(sess):
        return None

    user_id = meta.get('user_id')
    if not user_id:
        return None
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None

    _stripe_handle_checkout_completed(sess)
    return _login_user_from_db_row_after_payment(uid)


def _stripe_handle_subscription_updated(sub: object):
    sub_id = _stripe_resource_id(_stripe_get(sub, 'id'))
    if not sub_id:
        return
    status = _map_stripe_subscription_status(_stripe_get(sub, 'status') or '')
    price_id = None
    items = _stripe_get(sub, 'items')
    data = _stripe_get(items, 'data') if items is not None else None
    if data is None:
        data = []
    if not isinstance(data, (list, tuple)):
        try:
            data = list(data)
        except TypeError:
            data = []
    if data:
        price_obj = _stripe_get(data[0], 'price')
        price_id = _stripe_get(price_obj, 'id')
    plan = _stripe_price_to_plan(price_id)
    cpe = _stripe_get(sub, 'current_period_end')
    period_end = None
    if cpe is not None:
        try:
            period_end = datetime.fromtimestamp(int(cpe), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            period_end = None
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users SET
            subscription_status = %s,
            subscription_plan = COALESCE(%s, subscription_plan),
            subscription_current_period_end = COALESCE(%s, subscription_current_period_end)
        WHERE stripe_subscription_id = %s
        RETURNING id
        """,
        (status, plan, period_end, sub_id),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if row:
        _enforce_device_session_cap(row[0])


def _stripe_handle_subscription_deleted(sub: object):
    sub_id = _stripe_resource_id(_stripe_get(sub, 'id'))
    if not sub_id:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET subscription_status = %s WHERE stripe_subscription_id = %s",
        ('canceled', sub_id),
    )
    conn.commit()
    cur.close()
    conn.close()


@app.route('/')
def index():
    show_public_banner = not has_full_catalog_access()
    open_register = request.args.get('open_register')
    _, free_remaining = _public_daily_search_stats()
    return render_template(
        'index.html',
        show_public_banner=show_public_banner,
        open_register=open_register,
        free_daily_limit=FREE_DAILY_SEARCH_LIMIT,
        free_remaining_searches=free_remaining,
    )


@app.route('/terminos')
def terminos():
    return render_template('terminos.html')


@app.route('/privacidad')
def privacidad():
    return render_template('privacidad.html')


@app.route('/preguntas-frecuentes')
def preguntas_frecuentes():
    return render_template('preguntas_frecuentes.html')


@app.route('/catalogo-pdf/<path:filename>')
def catalogo_pdf(filename):
    """Sirve PDF solo si el usuario puede leer ese catálogo (no hay URL pública directa)."""
    resolved = _resolve_pdf_path(filename)
    if not resolved:
        return "PDF no encontrado", 404
    basename, full_path = resolved
    if not _user_may_read_pdf(basename):
        return "No autorizado para descargar este catálogo", 403
    return send_file(
        full_path,
        mimetype="application/pdf",
        as_attachment=False,
        max_age=0,
    )

@app.route('/api/health', methods=['GET'])
def health():
    """Endpoint de salud"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM catalogos')
        count = cur.fetchone()[0]
        
        cur.execute('SELECT catalogo_nombre, COUNT(*) as paginas FROM catalogos GROUP BY catalogo_nombre')
        catalogos = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'status': 'ok',
            'total_paginas': count,
            'catalogos': [{'nombre': c[0], 'paginas': c[1]} for c in catalogos]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def seleccionar_catalogo(year):
    year = int(year)

    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Obtener nombres únicos de catálogos
    cur.execute("""
        SELECT DISTINCT catalogo_nombre
        FROM catalogos
    """)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    catalogos = []

    for (nombre,) in rows:
        if not nombre:
            continue

        nombre = nombre.strip()

        # Caso 1: catálogo de un solo año → NAGS 2020.pdf
        import re
        match_single = re.search(r'(\d{4})', nombre)
        match_range = re.search(r'(\d{4})\s*-\s*(\d{4})', nombre)

        if match_range:
            start = int(match_range.group(1))
            end = int(match_range.group(2))
        elif match_single:
            start = end = int(match_single.group(1))
        else:
            continue

        catalogos.append({
            "nombre": nombre,
            "start": start,
            "end": end
        })

    # 2. Buscar coincidencias exactas
    matches = [c for c in catalogos if c["start"] <= year <= c["end"]]

    if matches:
        return max(matches, key=lambda c: c["start"])["nombre"]

    # 3. Si no hay match → usar el más cercano hacia abajo
    anteriores = [c for c in catalogos if c["end"] <= year]

    if anteriores:
        return max(anteriores, key=lambda c: c["end"])["nombre"]

    # 4. Si no hay nada
    return None

@app.route('/search', methods=['POST'])
def search():
    try:
        data = request.get_json(silent=True) or {}
        query = data.get('query', '').strip()

        if not query:
            return jsonify({'error': 'Consulta vacía'}), 400

        # Parsear consulta
        parsed = parse_search_query(query)
        print("DEBUG PARSED:", parsed)
        
        # Validar año obligatorio
        if parsed['error']:
            return jsonify({'error': parsed['error']}), 400
        
        year = parsed['year']
        marca = parsed['marca']
        modelo = parsed['modelo']
        terms = parsed.get('terms', [])

        if not terms:
            return jsonify({'error': 'Incluye marca o modelo además del año'}), 400

        # Público: catálogo fijo de prueba. Suscriptor activo: catálogo según año.
        if has_full_catalog_access():
            catalogo = seleccionar_catalogo(year)
            if not catalogo:
                return jsonify({'error': f'No hay catálogo para el año {year}'}), 404
            remaining_free_searches = None
        else:
            catalogo = PUBLIC_CATALOGO_NOMBRE
            _, remaining = _public_daily_search_stats()
            if remaining <= 0:
                return jsonify({
                    'error': (
                        f'Has alcanzado el límite diario de {FREE_DAILY_SEARCH_LIMIT} búsquedas '
                        'en la versión gratis. Activa tu suscripción para búsquedas ilimitadas.'
                    ),
                    'remaining_free_searches': 0,
                    'daily_free_limit': FREE_DAILY_SEARCH_LIMIT,
                }), 429
            _, remaining_free_searches = _consume_public_daily_search()

        # Priorizamos modelo (todo excepto primera palabra) por encima de marca
        primary_terms = terms[1:] if len(terms) > 1 else terms
        all_terms_text = " ".join(terms)
        primary_terms_text = " ".join(primary_terms)
        like_pattern = f"%{'%'.join(terms)}%"
        compact_terms = [_compact_token(t) for t in terms if _compact_token(t)]
        compact_terms = [t for t in compact_terms if len(t) >= 3]

        conn = get_db_connection()
        cur = conn.cursor()
        ranked_results = []

        try:
            # Query robusta: FTS + typo tolerance (trigram) + fallback parcial
            cur.execute(
                """
                WITH params AS (
                    SELECT
                        unaccent(lower(%s)) AS q_all,
                        unaccent(lower(%s)) AS q_primary,
                        %s::text[] AS terms,
                        %s::text[] AS compact_terms
                ),
                ranked AS (
                    SELECT
                        c.pagina,
                        c.pdf_path,
                        c.texto,
                        ts_rank_cd(
                            to_tsvector('simple', unaccent(lower(c.texto))),
                            plainto_tsquery('simple', p.q_all)
                        ) AS score_all,
                        ts_rank_cd(
                            to_tsvector('simple', unaccent(lower(c.texto))),
                            plainto_tsquery('simple', p.q_primary)
                        ) AS score_primary,
                        GREATEST(
                            similarity(unaccent(lower(c.texto)), p.q_all),
                            COALESCE((
                                SELECT MAX(word_similarity(t, unaccent(lower(c.texto))))
                                FROM unnest(p.terms) AS t
                            ), 0.0)
                        ) AS score_trgm,
                        CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM unnest(p.compact_terms) AS ct
                                WHERE regexp_replace(unaccent(lower(c.texto)), '[^a-z0-9]+', '', 'g') LIKE ('%%' || ct || '%%')
                            ) THEN 1.0
                            ELSE 0.0
                        END AS score_compact
                    FROM catalogos c
                    CROSS JOIN params p
                    WHERE c.catalogo_nombre = %s
                    AND (
                        to_tsvector('simple', unaccent(lower(c.texto))) @@ plainto_tsquery('simple', p.q_all)
                        OR to_tsvector('simple', unaccent(lower(c.texto))) @@ plainto_tsquery('simple', p.q_primary)
                        OR EXISTS (
                            SELECT 1
                            FROM unnest(p.terms) AS t
                            WHERE word_similarity(t, unaccent(lower(c.texto))) >=
                                CASE WHEN length(t) <= 3 THEN 0.70 ELSE 0.30 END
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM unnest(p.compact_terms) AS ct
                            WHERE regexp_replace(unaccent(lower(c.texto)), '[^a-z0-9]+', '', 'g') LIKE ('%%' || ct || '%%')
                        )
                        OR unaccent(lower(c.texto)) LIKE unaccent(lower(%s))
                    )
                )
                SELECT
                    pagina,
                    pdf_path,
                    texto,
                    score_all,
                    score_primary,
                    score_trgm,
                    score_compact,
                    (score_primary * 3.0 + score_all * 2.0 + score_trgm + score_compact * 2.0) AS final_score
                FROM ranked
                ORDER BY final_score DESC, pagina ASC
                LIMIT 5
                """,
                (all_terms_text, primary_terms_text, terms, compact_terms, catalogo, like_pattern),
            )
            ranked_results = cur.fetchall()
            search_strategy = "hybrid_fts_trgm"
        except psycopg2.errors.UndefinedFunction:
            conn.rollback()
            # Fallback seguro si faltan extensiones (unaccent/pg_trgm)
            cur.execute(
                """
                WITH params AS (
                    SELECT
                        lower(%s) AS q_all,
                        lower(%s) AS q_primary,
                        %s::text[] AS compact_terms
                ),
                ranked AS (
                    SELECT
                        c.pagina,
                        c.pdf_path,
                        c.texto,
                        ts_rank_cd(
                            to_tsvector('simple', lower(c.texto)),
                            plainto_tsquery('simple', p.q_all)
                        ) AS score_all,
                        ts_rank_cd(
                            to_tsvector('simple', lower(c.texto)),
                            plainto_tsquery('simple', p.q_primary)
                        ) AS score_primary,
                        CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM unnest(p.compact_terms) AS ct
                                WHERE regexp_replace(lower(c.texto), '[^a-z0-9]+', '', 'g') LIKE ('%%' || ct || '%%')
                            ) THEN 1.0
                            ELSE 0.0
                        END AS score_compact
                    FROM catalogos c
                    CROSS JOIN params p
                    WHERE c.catalogo_nombre = %s
                    AND (
                        to_tsvector('simple', lower(c.texto)) @@ plainto_tsquery('simple', p.q_all)
                        OR to_tsvector('simple', lower(c.texto)) @@ plainto_tsquery('simple', p.q_primary)
                        OR EXISTS (
                            SELECT 1
                            FROM unnest(p.compact_terms) AS ct
                            WHERE regexp_replace(lower(c.texto), '[^a-z0-9]+', '', 'g') LIKE ('%%' || ct || '%%')
                        )
                        OR lower(c.texto) LIKE lower(%s)
                    )
                )
                SELECT
                    pagina,
                    pdf_path,
                    texto,
                    score_all,
                    score_primary,
                    score_compact,
                    0.0 AS score_trgm,
                    (score_primary * 3.0 + score_all * 2.0 + score_compact * 2.0) AS final_score
                FROM ranked
                ORDER BY final_score DESC, pagina ASC
                LIMIT 5
                """,
                (all_terms_text, primary_terms_text, compact_terms, catalogo, like_pattern),
            )
            ranked_results = cur.fetchall()
            search_strategy = "fts_fallback"

        cur.close()
        conn.close()

        if not ranked_results:
            return jsonify({
                'found': False,
                'message': f'No se encontró en {catalogo}',
                'year': year,
                'marca': marca or '',
                'modelo': modelo or '',
                'search_strategy': search_strategy,
                'search_terms': terms,
                'remaining_free_searches': remaining_free_searches,
                'daily_free_limit': FREE_DAILY_SEARCH_LIMIT if not has_full_catalog_access() else None,
            })

        best = ranked_results[0]
        page, pdf_path, texto, score_all, score_primary, score_trgm, score_compact, final_score = best
        candidates = []
        for row in ranked_results:
            r_page, r_pdf_path, r_text, r_score_all, r_score_primary, r_score_trgm, r_score_compact, r_final_score = row
            candidates.append({
                'page': r_page,
                'pdf_name': _stored_pdf_basename(r_pdf_path),
                'score': round(float(r_final_score), 6),
                'score_all': round(float(r_score_all), 6),
                'score_primary': round(float(r_score_primary), 6),
                'score_trgm': round(float(r_score_trgm), 6),
                'score_compact': round(float(r_score_compact), 6),
                'preview': (r_text[:120] + "...") if len(r_text) > 120 else r_text,
            })

        return jsonify({
            'found': True,
            'year': year,
            'page': page,
            'pdf_name': _stored_pdf_basename(pdf_path),
            'marca': marca or '',
            'modelo': modelo or '',
            'search_strategy': search_strategy,
            'search_terms': terms,
            'score': round(float(final_score), 6),
            'score_all': round(float(score_all), 6),
            'score_primary': round(float(score_primary), 6),
            'score_trgm': round(float(score_trgm), 6),
            'score_compact': round(float(score_compact), 6),
            'top_matches': candidates,
            'message': f'Encontrado en página {page}',
            'preview': texto[:200] + '...' if len(texto) > 200 else texto,
            'remaining_free_searches': remaining_free_searches,
            'daily_free_limit': FREE_DAILY_SEARCH_LIMIT if not has_full_catalog_access() else None,
        })

    except Exception as e:
        print(f"Error en búsqueda: {str(e)}")  # Log del error
        return jsonify({'error': str(e)}), 500

@app.route('/pdf/<path:pdf_name>/<int:page>')
def serve_pdf_file(pdf_name, page):
    resolved = _resolve_pdf_path(pdf_name)
    if not resolved:
        print(f"❌ PDF inválido o no existe: {pdf_name}")
        return "PDF no encontrado", 404
    basename, pdf_path = resolved
    if not _user_may_read_pdf(basename):
        return "No autorizado para ver este catálogo", 403

    print(f"✅ Abriendo PDF: {pdf_path} en página {page}")
    match_year = re.search(r"(19\d{2}|20\d{2})", basename)
    year = int(match_year.group(1)) if match_year else 0
    return render_template(
        "viewer.html",
        pdf_name=basename,
        page=page,
        year=year,
        can_navigate_catalog=has_full_catalog_access(),
    )

def _dashboard_require_active():
    if current_user.subscription_status != 'active':
        flash('Tu suscripción no está activa. No puedes acceder al panel.')
        return redirect(url_for('index'))
    return None


@app.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    redir = _dashboard_require_active()
    if redir:
        return redir

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT username, email, subscription_status, subscription_plan, subscription_current_period_end
        FROM users WHERE id = %s
        """,
        (current_user.id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        flash('No se pudo cargar tu cuenta.')
        return redirect(url_for('index'))

    cur.execute(
        """
        SELECT id, created_at, last_seen_at, user_agent, token_hash
        FROM user_sessions
        WHERE user_id = %s
        ORDER BY last_seen_at DESC
        """,
        (current_user.id,),
    )
    sess_rows = cur.fetchall()
    cur.close()
    conn.close()

    def _fmt_dt(dt):
        if not dt:
            return '—'
        try:
            return dt.astimezone(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
        except Exception:
            return str(dt)

    current_hash = _hash_reset_token(session.get('device_token') or '')
    sessions_out = []
    for sid, created_at, last_seen, ua, th in sess_rows:
        sessions_out.append(
            {
                'id': sid,
                'created_at': _fmt_dt(created_at),
                'last_seen_at': _fmt_dt(last_seen),
                'user_agent': (ua or '')[:160] or '—',
                'is_current': th == current_hash,
            }
        )

    username, email, sub_status, sub_plan, period_end = row
    period_label = None
    if period_end:
        try:
            period_label = period_end.astimezone(timezone.utc).strftime('%d/%m/%Y %H:%M') + ' UTC'
        except Exception:
            period_label = str(period_end)

    cap = _max_device_sessions(sub_plan, sub_status)

    return render_template(
        'dashboard.html',
        dash_username=username,
        dash_email=email,
        subscription_status=sub_status,
        subscription_plan=sub_plan,
        subscription_plan_label=_plan_label(sub_plan),
        period_end_label=period_label,
        device_sessions=sessions_out,
        device_cap=cap,
    )


@app.route('/dashboard/perfil', methods=['POST'])
@login_required
def dashboard_update_profile():
    redir = _dashboard_require_active()
    if redir:
        return redir
    new_username = (request.form.get('username') or '').strip()
    if len(new_username) < 2:
        flash('El usuario debe tener al menos 2 caracteres.')
        return redirect(url_for('dashboard'))
    if len(new_username) > 80:
        flash('El usuario es demasiado largo.')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET username = %s WHERE id = %s",
            (new_username, current_user.id),
        )
        if cur.rowcount == 0:
            flash('No se pudo actualizar el usuario.')
        else:
            conn.commit()
            flash('Usuario actualizado.')
    except pg_errors.UniqueViolation:
        conn.rollback()
        flash('Ese nombre de usuario ya está en uso.')
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('dashboard'))


@app.route('/dashboard/sesiones/cerrar-otras', methods=['POST'])
@login_required
def dashboard_revoke_other_sessions():
    redir = _dashboard_require_active()
    if redir:
        return redir
    tok = session.get('device_token')
    if not tok:
        flash('No hay sesión activa en este dispositivo.')
        return redirect(url_for('dashboard'))
    _revoke_other_device_sessions(current_user.id, tok)
    flash('Se cerraron las demás sesiones.')
    return redirect(url_for('dashboard'))


@app.route('/dashboard/stripe-portal', methods=['POST'])
@login_required
def dashboard_stripe_portal():
    """
    Abre el Customer / Billing Portal de Stripe (página alojada por Stripe).
    Activa en Stripe Dashboard → Configuración → Portal de facturación del cliente:
      suscripción, método de pago, facturas, cancelación al final del periodo,
      reactivación cuando Stripe lo permita y cambio de plan si configuraste precios elegibles.
    """
    redir = _dashboard_require_active()
    if redir:
        return redir
    if not stripe.api_key:
        flash('El servicio de pagos no está configurado. Intenta más tarde.')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT stripe_customer_id FROM users WHERE id = %s",
        (current_user.id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    customer_id = row[0] if row else None
    if not customer_id:
        flash(
            'Tu cuenta aún no tiene un cliente de facturación vinculado. '
            'Si acabas de pagar, espera unos minutos o escribe a ' + (os.getenv('LEGAL_CONTACT_EMAIL') or 'contacto@tudominio.com')
        )
        return redirect(url_for('dashboard'))

    base = _absolute_base_url()
    return_url = base + url_for('dashboard')
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
    except stripe.StripeError as e:
        flash(getattr(e, 'user_message', None) or str(e))
        return redirect(url_for('dashboard'))

    return redirect(portal_session.url, code=303)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form.get('identifier')  # email o username
        password = request.form.get('password')

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id, username, email, password_hash, subscription_status, subscription_plan
            FROM users WHERE email = %s OR username = %s
            """,
            (identifier, identifier),
        )

        user = cur.fetchone()

        cur.close()
        conn.close()

        if user and not user[3]:
            flash(
                'Tu cuenta aún no tiene contraseña. Revisa tu correo (y spam) por el mensaje '
                '«Activa tu cuenta» tras tu pago, o usa «Olvidé mi contraseña» con el mismo email.'
            )
            return redirect(url_for('login'))

        if user and bcrypt.check_password_hash(user[3], password):
            user_obj = User(*user)
            remember = True if request.form.get('remember') == 'on' else False
            login_user(user_obj, remember=remember)
            try:
                _register_device_session(
                    user_obj.id,
                    user_obj.subscription_plan,
                    user_obj.subscription_status,
                    remember,
                    request.headers.get('User-Agent'),
                )
            except Exception as exc:
                print('device session:', exc)
                session.pop('device_token', None)
                logout_user()
                flash('No se pudo iniciar sesión. Intenta de nuevo o ejecuta la migración 004 en PostgreSQL.')
                return redirect(url_for('login'))
            return redirect(url_for('index'))

        flash('Credenciales incorrectas')

    return render_template('login.html')


@app.route('/olvidaste-contrasena', methods=['GET', 'POST'])
def forgot_password():
    """
    Solicitud de restablecimiento.
    Siempre responde con el mismo mensaje para evitar enumeración de cuentas.
    """
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()

        # Mensaje genérico (no confirmar existencia).
        generic_msg = 'Si existe una cuenta asociada, te enviaremos un enlace para restablecer tu contraseña.'

        if not email:
            flash(generic_msg)
            return redirect(url_for('forgot_password'))

        token = secrets.token_urlsafe(32)
        token_hash = _hash_reset_token(token)

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Guarda token y expiración solo si el email existe.
            cur.execute(
                """
                UPDATE users
                SET password_reset_token_hash = %s,
                    password_reset_expires_at = (NOW() + INTERVAL '30 minutes'),
                    password_reset_used_at = NULL
                WHERE email = %s
                """,
                (token_hash, email),
            )
            conn.commit()
            rows = cur.rowcount
        finally:
            cur.close()
            conn.close()

        # Si no existe el email, no enviamos nada, pero respondemos igual.
        if rows:
            reset_url = _password_reset_url(token)
            subject = 'Restablece tu contraseña'
            body = (
                "Recibimos una solicitud para restablecer tu contraseña.\n\n"
                f"Enlace (válido por 30 minutos):\n{reset_url}\n\n"
                "Si no fuiste tú, ignora este correo.\n"
            )
            sent = False
            try:
                sent = _send_email(email, subject, body)
            except Exception:
                sent = False

            if not sent:
                # En desarrollo o sin SMTP, queda en logs del servidor.
                print("PASSWORD RESET URL:", reset_url)

        flash(generic_msg)
        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')


@app.route('/restablecer/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token = (token or '').strip()
    if not token:
        return render_template('reset_password.html', error='Enlace inválido.'), 400

    token_hash = _hash_reset_token(token)
    error = None

    if request.method == 'POST':
        password = request.form.get('password') or ''
        password2 = request.form.get('password2') or ''

        if len(password) < 8:
            error = 'La contraseña debe tener al menos 8 caracteres.'
        elif password != password2:
            error = 'Las contraseñas no coinciden.'
        else:
            conn = get_db_connection()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT id
                    FROM users
                    WHERE password_reset_token_hash = %s
                      AND password_reset_used_at IS NULL
                      AND password_reset_expires_at IS NOT NULL
                      AND password_reset_expires_at >= NOW()
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
                if not row:
                    error = 'Este enlace es inválido o ya expiró. Solicita uno nuevo.'
                else:
                    user_id = row[0]
                    new_hash = bcrypt.generate_password_hash(password).decode('utf-8')
                    cur.execute(
                        """
                        UPDATE users
                        SET password_hash = %s,
                            password_reset_used_at = NOW(),
                            password_reset_token_hash = NULL,
                            password_reset_expires_at = NULL
                        WHERE id = %s
                        """,
                        (new_hash, user_id),
                    )
                    try:
                        cur.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))
                    except Exception:
                        pass
                    conn.commit()
                    flash('Contraseña actualizada. Ya puedes iniciar sesión.')
                    return redirect(url_for('login'))
            finally:
                cur.close()
                conn.close()

    else:
        # Validación rápida para mostrar error inmediato en GET si ya expiró.
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT 1
                FROM users
                WHERE password_reset_token_hash = %s
                  AND password_reset_used_at IS NULL
                  AND password_reset_expires_at IS NOT NULL
                  AND password_reset_expires_at >= NOW()
                """,
                (token_hash,),
            )
            ok = cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()
        if not ok:
            error = 'Este enlace es inválido o ya expiró. Solicita uno nuevo.'

    return render_template('reset_password.html', error=error)

@app.route('/register')
def register():
    """Abre el flujo de registro en la página principal (modal)."""
    return redirect(url_for('index', open_register='1'))


@app.route('/api/completar-cuenta-poll', methods=['GET'])
def api_completar_cuenta_poll():
    """Estado del pago para reintentos en /completar-cuenta (JSON)."""
    if not stripe.api_key:
        return jsonify({'status': 'invalid'}), 503

    session_id = (request.args.get('session_id') or '').strip()
    sess = _retrieve_pay_first_checkout_session(session_id)
    if not sess:
        return jsonify({'status': 'invalid'})
    if _checkout_session_payment_ready(sess):
        return jsonify({'status': 'ready'})
    return jsonify({'status': 'pending'})


@app.route('/api/subscription-success-poll', methods=['GET'])
def api_subscription_success_poll():
    """Estado del pago para autologin en /subscription/success (registro / checkout mensual)."""
    if not stripe.api_key:
        return jsonify({'status': 'invalid'}), 503

    session_id = (request.args.get('session_id') or '').strip()
    if not session_id:
        return jsonify({'status': 'invalid'})
    try:
        sess = stripe.checkout.Session.retrieve(session_id, expand=['customer'])
    except stripe.StripeError:
        return jsonify({'status': 'invalid'})

    if _stripe_get(sess, 'mode') != 'subscription':
        return jsonify({'status': 'invalid'})

    meta = _stripe_meta_dict(sess)
    if (meta.get('flow') or '').strip().lower() == 'pay_first':
        return jsonify({'status': 'pay_first'})

    if _checkout_session_payment_ready(sess):
        if meta.get('user_id'):
            return jsonify({'status': 'ready'})
        return jsonify({'status': 'invalid'})

    return jsonify({'status': 'pending'})


@app.route('/completar-cuenta', methods=['GET', 'POST'])
def completar_cuenta():
    """Tras pagar desde el modal de límite (pay-first): usuario y contraseña (email viene de Stripe)."""
    if not stripe.api_key:
        flash('El servicio de pagos no está configurado.')
        return redirect(url_for('index'))

    session_id = (request.values.get('session_id') or '').strip()
    if request.method == 'POST':
        session_id = (request.form.get('session_id') or '').strip()

    if not session_id:
        flash('Enlace inválido.')
        return redirect(url_for('index'))

    sess = _retrieve_pay_first_checkout_session(session_id)
    if not sess:
        flash('Enlace inválido o sesión expirada.')
        return redirect(url_for('index'))

    if not _checkout_session_payment_ready(sess):
        if request.method == 'POST':
            flash('Tu pago aún se está confirmando. Espera unos segundos en esta pantalla.')
            return redirect(url_for('completar_cuenta', session_id=session_id))
        email_hint = (_checkout_email_from_session(sess) or '').strip()
        return render_template(
            'completar_cuenta.html',
            waiting_payment=True,
            session_id=session_id,
            email_hint=email_hint,
        )

    uid = _pay_first_sync_user_from_session(sess)
    if not uid:
        flash('No pudimos preparar tu cuenta. Si el cargo apareció en tu tarjeta, escríbenos con el email de pago.')
        return redirect(url_for('index'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT email, username, password_hash FROM users WHERE id = %s',
        (uid,),
    )
    urow = cur.fetchone()
    cur.close()
    conn.close()
    if not urow:
        flash('Cuenta no encontrada.')
        return redirect(url_for('index'))
    email, username_provisional, pwd_hash = urow[0], urow[1], urow[2]

    if pwd_hash:
        return _login_user_from_db_row_after_payment(uid)

    error = None
    if request.method == 'POST':
        new_user = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        password2 = request.form.get('password2') or ''

        if len(new_user) < 2:
            error = 'El usuario debe tener al menos 2 caracteres.'
        elif len(new_user) > 80:
            error = 'El usuario es demasiado largo.'
        elif not re.match(r'^[a-zA-Z0-9_-]+$', new_user):
            error = 'Usuario: solo letras, números, guión y guión bajo.'
        elif len(password) < 8:
            error = 'La contraseña debe tener al menos 8 caracteres.'
        elif password != password2:
            error = 'Las contraseñas no coinciden.'
        else:
            conn = get_db_connection()
            cur = conn.cursor()
            try:
                cur.execute(
                    'SELECT id FROM users WHERE lower(username) = lower(%s) AND id != %s',
                    (new_user, uid),
                )
                if cur.fetchone():
                    error = 'Ese nombre de usuario ya está en uso.'
                else:
                    hashed = bcrypt.generate_password_hash(password).decode('utf-8')
                    cur.execute(
                        """
                        UPDATE users SET username = %s, password_hash = %s,
                            password_reset_token_hash = NULL,
                            password_reset_expires_at = NULL,
                            password_reset_used_at = NULL
                        WHERE id = %s AND password_hash IS NULL
                        RETURNING id
                        """,
                        (new_user, hashed, uid),
                    )
                    if not cur.fetchone():
                        conn.rollback()
                        cur.execute(
                            """
                            SELECT id, username, email, password_hash, subscription_status, subscription_plan
                            FROM users WHERE id = %s
                            """,
                            (uid,),
                        )
                        row_done = cur.fetchone()
                        conn.commit()
                        if row_done and row_done[3]:
                            return _login_user_from_db_row_after_payment(uid)
                        flash('Tu cuenta ya fue activada. Inicia sesión.')
                        return redirect(url_for('login'))
                    conn.commit()
                    return _login_user_from_db_row_after_payment(uid)
            finally:
                cur.close()
                conn.close()

    username_value = (
        (request.form.get('username') or '').strip()
        if request.method == 'POST' and error
        else username_provisional
    )

    return render_template(
        'completar_cuenta.html',
        waiting_payment=False,
        session_id=session_id,
        email=email,
        username_value=username_value,
        error=error,
    )


@app.route('/api/register-checkout', methods=['POST'])
def api_register_checkout():
    """Crea usuario (pendiente de pago) y devuelve URL de Stripe Checkout (suscripción)."""
    if not stripe.api_key:
        return jsonify({'error': 'Configura STRIPE_SECRET_KEY en el entorno.'}), 503

    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    plan = (data.get('plan') or '').strip().lower()

    if not username or not email or not password:
        return jsonify({'error': 'Completa usuario, email y contraseña.'}), 400
    if len(password) < 8:
        return jsonify({'error': 'La contraseña debe tener al menos 8 caracteres.'}), 400
    if plan not in ('monthly', 'annual'):
        return jsonify({'error': 'Selecciona plan mensual o anual.'}), 400

    price_id = _price_id_for_plan(plan)
    if not price_id:
        return jsonify({'error': 'Configura STRIPE_PRICE_MONTHLY y STRIPE_PRICE_ANNUAL (Price IDs de Stripe).'}), 503

    hashed = bcrypt.generate_password_hash(password).decode('utf-8')
    conn = get_db_connection()
    cur = conn.cursor()
    user_id = None

    try:
        # Plan elegido en checkout; el estado pending_payment requiere migración 007 en la BD
        # si el CHECK valid_subscription solo permitía active/inactive/expired.
        cur.execute(
            """
            INSERT INTO users (
                username, email, password_hash, subscription_status,
                subscription_plan, subscription_current_period_end
            )
            VALUES (%s, %s, %s, %s, %s, NULL)
            RETURNING id
            """,
            (username, email, hashed, 'pending_payment', plan),
        )
        user_id = cur.fetchone()[0]
        conn.commit()
    except pg_errors.UniqueViolation:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'error': 'Ese usuario o email ya está registrado.'}), 409
    except pg_errors.CheckViolation:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({
            'error': (
                'La base de datos rechazó el registro (CHECK valid_subscription). '
                'Ejecuta migrations/007_valid_subscription_status.sql en PostgreSQL.'
            ),
        }), 500
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        err = str(e).lower()
        if 'stripe_customer_id' in err or 'column' in err:
            return jsonify({
                'error': 'Ejecuta migrations/001_stripe_columns.sql en PostgreSQL o revisa el esquema users.',
            }), 500
        raise

    cur.close()
    conn.close()

    base = _absolute_base_url()
    try:
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=base + '/subscription/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=base + '/?register_canceled=1',
            client_reference_id=str(user_id),
            customer_email=email,
            metadata={'user_id': str(user_id), 'plan': plan},
            subscription_data={
                'metadata': {'user_id': str(user_id), 'plan': plan},
            },
        )
    except stripe.StripeError as e:
        return jsonify({'error': getattr(e, 'user_message', None) or str(e)}), 502

    return jsonify({'checkout_url': checkout_session.url})


@app.route('/api/checkout-pay-first-monthly', methods=['POST'])
def api_checkout_pay_first_monthly():
    """Invitado: Checkout mensual sin registro previo; tras pagar completa usuario/contraseña en /completar-cuenta."""
    if not stripe.api_key:
        return jsonify({'error': 'Configura STRIPE_SECRET_KEY en el entorno.'}), 503

    price_id = _price_id_for_plan('monthly')
    if not price_id:
        return jsonify({'error': 'Configura STRIPE_PRICE_MONTHLY (Price ID de Stripe).'}), 503

    base = _absolute_base_url()
    try:
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=base + '/completar-cuenta?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=base + '/?limit_checkout_canceled=1',
            metadata={'flow': 'pay_first', 'plan': 'monthly'},
            subscription_data={
                'metadata': {'plan': 'monthly', 'flow': 'pay_first'},
            },
        )
    except stripe.StripeError as e:
        return jsonify({'error': getattr(e, 'user_message', None) or str(e)}), 502

    return jsonify({'checkout_url': checkout_session.url})


@app.route('/api/checkout-monthly', methods=['POST'])
@login_required
def api_checkout_monthly():
    """Usuario ya registrado sin suscripción activa: Checkout Stripe plan mensual."""
    if not stripe.api_key:
        return jsonify({'error': 'Configura STRIPE_SECRET_KEY en el entorno.'}), 503
    if current_user.subscription_status == 'active':
        return jsonify({'error': 'Tu suscripción ya está activa.'}), 400

    price_id = _price_id_for_plan('monthly')
    if not price_id:
        return jsonify({'error': 'Configura STRIPE_PRICE_MONTHLY (Price ID de Stripe).'}), 503

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT email, stripe_customer_id FROM users WHERE id = %s",
        (current_user.id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({'error': 'Usuario no encontrado.'}), 404

    email, stripe_customer_id = row[0], row[1]
    base = _absolute_base_url()
    uid = current_user.id
    try:
        payload = {
            'mode': 'subscription',
            'line_items': [{'price': price_id, 'quantity': 1}],
            'success_url': base + '/subscription/success?session_id={CHECKOUT_SESSION_ID}',
            'cancel_url': base + '/?limit_checkout_canceled=1',
            'client_reference_id': str(uid),
            'metadata': {'user_id': str(uid), 'plan': 'monthly'},
            'subscription_data': {
                'metadata': {'user_id': str(uid), 'plan': 'monthly'},
            },
        }
        scid = (stripe_customer_id or '').strip()
        if scid:
            payload['customer'] = scid
        else:
            payload['customer_email'] = (email or '').strip().lower()
        checkout_session = stripe.checkout.Session.create(**payload)
    except stripe.StripeError as e:
        return jsonify({'error': getattr(e, 'user_message', None) or str(e)}), 502

    return jsonify({'checkout_url': checkout_session.url})


@app.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook():
    wh_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    if not wh_secret:
        return jsonify({'error': 'STRIPE_WEBHOOK_SECRET no configurado.'}), 503

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    if not sig_header:
        return jsonify({'error': 'Sin cabecera Stripe-Signature.'}), 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, wh_secret)
    except ValueError:
        return jsonify({'error': 'Payload inválido.'}), 400
    except stripe.SignatureVerificationError:
        return jsonify({'error': 'Firma inválida.'}), 400

    etype = event['type']
    obj = event['data']['object']

    if etype == 'checkout.session.completed':
        _stripe_handle_checkout_completed(obj)
    elif etype == 'customer.subscription.updated':
        _stripe_handle_subscription_updated(obj)
    elif etype == 'customer.subscription.deleted':
        _stripe_handle_subscription_deleted(obj)

    return jsonify({'received': True}), 200


@app.route('/subscription/success')
def subscription_success():
    session_id = (request.args.get('session_id') or '').strip()
    auto = _try_autologin_from_checkout_session_id(session_id)
    if auto is not None:
        return auto
    from_poll = (request.args.get('from_poll') or '').strip() == '1'
    return render_template(
        'subscription_success.html',
        session_id=session_id or None,
        from_poll=from_poll,
    )


@app.route('/logout')
@login_required
def logout():
    tok = session.pop('device_token', None)
    uid = current_user.id
    try:
        if tok:
            _revoke_device_session(uid, tok)
    except Exception:
        pass
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    print('🚀 Iniciando servidor Flask...')
    print('📊 Health check: http://localhost:5001/api/health')
    print('🔍 Búsqueda: POST http://localhost:5001/search')
    print('🌐 Frontend: http://localhost:5001/')
    print('💳 Webhook Stripe (local): stripe listen --forward-to localhost:5001/webhooks/stripe')
    app.run(debug=True, host='0.0.0.0', port=5001)