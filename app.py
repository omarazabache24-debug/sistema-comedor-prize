# -*- coding: utf-8 -*-
"""
Sistema Comedor PRIZE - Interfaz PRO
Archivo único app.py para Render / local.

Usuarios demo:
- adm1 / adm1
- adm2 / adm2
- admin / admin123
- comedor / comedor123

Dependencias recomendadas en requirements.txt:
Flask
pandas
openpyxl
gunicorn
"""

import os
import re
import sqlite3
import smtplib
try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None
from io import BytesIO
from datetime import datetime, date
from functools import wraps
from email.message import EmailMessage

import pandas as pd
from openpyxl import load_workbook
from flask import (
    Flask, request, redirect, url_for, session, send_file,
    render_template_string, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash


# =========================
# CONFIGURACIÓN
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
REPORT_DIR = os.path.join(BASE_DIR, "reportes_cierre")
CONCESIONARIA_DIR = os.path.join(BASE_DIR, "consumos_concesionaria")
ENTREGAS_DIR = os.path.join(BASE_DIR, "reportes_entrega")
DB_PATH = os.path.join(BASE_DIR, "comedor_prize.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(CONCESIONARIA_DIR, exist_ok=True)
os.makedirs(ENTREGAS_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "prize-comedor-pro-2026")


@app.errorhandler(500)
def internal_error(e):
    try:
        app.logger.exception("Error interno controlado: %s", e)
        flash("Se detecto un error interno. Revisa que el Excel tenga columnas validas: EMPRESA, DNI, NOMBRE, CARGO y AREA. El sistema no perdio informacion.", "error")
        return redirect(request.referrer or url_for("dashboard"))
    except Exception:
        return "Error interno controlado. Vuelve al menu principal e intenta nuevamente.", 500


# =========================
# BASE DE DATOS PERSISTENTE
# Render: PostgreSQL con DATABASE_URL. Local: SQLite de respaldo.
# =========================
def _sql(sql):
    return sql.replace("?", "%s") if USE_POSTGRES else sql

def get_conn():
    if USE_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("Falta psycopg2-binary en requirements.txt")
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def q_all(sql, params=()):
    with get_conn() as conn:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(_sql(sql), params)
                return cur.fetchall()
        return conn.execute(sql, params).fetchall()

def q_one(sql, params=()):
    rows = q_all(sql, params)
    return rows[0] if rows else None

def q_exec(sql, params=()):
    with get_conn() as conn:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(_sql(sql), params)
                conn.commit()
                return None
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid

def audit_event(accion, tabla='', registro_id='', detalle=''):
    try:
        q_exec("INSERT INTO auditoria(usuario,accion,tabla,registro_id,detalle) VALUES(?,?,?,?,?)",
               (session.get('user','sistema'), accion, tabla, str(registro_id or ''), detalle or ''))
    except Exception:
        pass

def init_db():
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    password_plain TEXT DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'comedor',
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS trabajadores (
                    id SERIAL PRIMARY KEY,
                    empresa TEXT DEFAULT 'PRIZE',
                    dni TEXT UNIQUE NOT NULL,
                    nombre TEXT NOT NULL,
                    cargo TEXT DEFAULT '',
                    area TEXT DEFAULT '',
                    activo INTEGER NOT NULL DEFAULT 1,
                    creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    actualizado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS consumos (
                    id SERIAL PRIMARY KEY,
                    fecha TEXT NOT NULL,
                    hora TEXT NOT NULL,
                    dni TEXT NOT NULL,
                    trabajador TEXT DEFAULT '',
                    empresa TEXT DEFAULT 'PRIZE',
                    area TEXT DEFAULT '',
                    tipo TEXT DEFAULT 'Almuerzo',
                    cantidad INTEGER DEFAULT 1,
                    precio_unitario REAL DEFAULT 10,
                    total REAL DEFAULT 10,
                    observacion TEXT DEFAULT '',
                    estado TEXT DEFAULT 'PENDIENTE',
                    creado_por TEXT DEFAULT '',
                    entregado_por TEXT DEFAULT '',
                    entregado_en TEXT DEFAULT '',
                    comedor TEXT DEFAULT 'Comedor 01',
                    fundo TEXT DEFAULT 'Kawsay Allpa',
                    responsable TEXT DEFAULT '',
                    adicional INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS auditoria (
                    id SERIAL PRIMARY KEY,
                    fecha_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    usuario TEXT DEFAULT '',
                    accion TEXT DEFAULT '',
                    tabla TEXT DEFAULT '',
                    registro_id TEXT DEFAULT '',
                    detalle TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS cierres (
                    id SERIAL PRIMARY KEY,
                    fecha TEXT UNIQUE NOT NULL,
                    cerrado_por TEXT DEFAULT '',
                    cerrado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    total_consumos INTEGER DEFAULT 0,
                    total_entregados INTEGER DEFAULT 0,
                    total_pendientes INTEGER DEFAULT 0,
                    total_importe REAL DEFAULT 0,
                    archivo_excel TEXT DEFAULT '',
                    correo_destino TEXT DEFAULT '',
                    correo_estado TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS importaciones (
                    id SERIAL PRIMARY KEY,
                    fecha_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    archivo TEXT DEFAULT '',
                    total INTEGER DEFAULT 0,
                    creados INTEGER DEFAULT 0,
                    errores INTEGER DEFAULT 0,
                    usuario TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS configuracion (
                    clave TEXT PRIMARY KEY,
                    valor TEXT DEFAULT ''
                );
                """)
                for stmt in [
                    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS password_plain TEXT DEFAULT ''",
                    "ALTER TABLE consumos ADD COLUMN IF NOT EXISTS comedor TEXT DEFAULT 'Comedor 01'",
                    "ALTER TABLE consumos ADD COLUMN IF NOT EXISTS fundo TEXT DEFAULT 'Kawsay Allpa'",
                    "ALTER TABLE consumos ADD COLUMN IF NOT EXISTS responsable TEXT DEFAULT ''",
                    "ALTER TABLE consumos ADD COLUMN IF NOT EXISTS adicional INTEGER DEFAULT 0",
                ]:
                    cur.execute(stmt)
                cur.execute("""
                    DELETE FROM consumos c USING consumos d
                    WHERE COALESCE(c.adicional,0)=0 AND COALESCE(d.adicional,0)=0
                      AND c.fecha=d.fecha AND c.dni=d.dni AND c.id>d.id
                """)
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_consumo_unico_dni_fecha ON consumos(fecha, dni) WHERE COALESCE(adicional,0)=0")
                conn.commit()
    else:
        with get_conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_plain TEXT DEFAULT '',
                role TEXT NOT NULL DEFAULT 'comedor',
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS trabajadores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa TEXT DEFAULT 'PRIZE',
                dni TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                cargo TEXT DEFAULT '',
                area TEXT DEFAULT '',
                activo INTEGER NOT NULL DEFAULT 1,
                creado TEXT DEFAULT CURRENT_TIMESTAMP,
                actualizado TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS consumos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                hora TEXT NOT NULL,
                dni TEXT NOT NULL,
                trabajador TEXT DEFAULT '',
                empresa TEXT DEFAULT 'PRIZE',
                area TEXT DEFAULT '',
                tipo TEXT DEFAULT 'Almuerzo',
                cantidad INTEGER DEFAULT 1,
                precio_unitario REAL DEFAULT 10,
                total REAL DEFAULT 10,
                observacion TEXT DEFAULT '',
                estado TEXT DEFAULT 'PENDIENTE',
                creado_por TEXT DEFAULT '',
                entregado_por TEXT DEFAULT '',
                entregado_en TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP,
                usuario TEXT DEFAULT '',
                accion TEXT DEFAULT '',
                tabla TEXT DEFAULT '',
                registro_id TEXT DEFAULT '',
                detalle TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS cierres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT UNIQUE NOT NULL,
                cerrado_por TEXT DEFAULT '',
                cerrado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                total_consumos INTEGER DEFAULT 0,
                total_entregados INTEGER DEFAULT 0,
                total_pendientes INTEGER DEFAULT 0,
                total_importe REAL DEFAULT 0,
                archivo_excel TEXT DEFAULT '',
                correo_destino TEXT DEFAULT '',
                correo_estado TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS importaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP,
                archivo TEXT DEFAULT '',
                total INTEGER DEFAULT 0,
                creados INTEGER DEFAULT 0,
                errores INTEGER DEFAULT 0,
                usuario TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS configuracion (
                clave TEXT PRIMARY KEY,
                valor TEXT DEFAULT ''
            );
            """)
            user_cols = [x["name"] for x in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
            if "password_plain" not in user_cols:
                conn.execute("ALTER TABLE usuarios ADD COLUMN password_plain TEXT DEFAULT ''")
            cols = [x["name"] for x in conn.execute("PRAGMA table_info(consumos)").fetchall()]
            for col, sqltype, default in [("comedor", "TEXT", "'Comedor 01'"), ("fundo", "TEXT", "'Kawsay Allpa'"), ("responsable", "TEXT", "''"), ("adicional", "INTEGER", "0")]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE consumos ADD COLUMN {col} {sqltype} DEFAULT {default}")
            try:
                conn.execute("""
                    DELETE FROM consumos
                    WHERE id NOT IN (
                        SELECT MIN(id) FROM consumos WHERE COALESCE(adicional,0)=0 GROUP BY fecha,dni
                        UNION
                        SELECT id FROM consumos WHERE COALESCE(adicional,0)=1
                    )
                """)
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_consumo_unico_dni_fecha ON consumos(fecha, dni) WHERE adicional=0")
            except Exception:
                pass
            conn.commit()

    defaults = {"bloqueo_activo": "0", "hora_inicio": "00:00", "hora_fin": "23:59", "clave_quitar": "1234"}
    for k, v in defaults.items():
        if not q_one("SELECT clave FROM configuracion WHERE clave=?", (k,)):
            q_exec("INSERT INTO configuracion(clave,valor) VALUES(?,?)", (k, v))

    for username, password, role in [("adm", "@123", "admin"), ("adm1", "adm1", "admin"), ("adm2", "adm2", "admin"), ("admin", "admin123", "admin"), ("comedor", "comedor123", "comedor")]:
        existe = q_one("SELECT id FROM usuarios WHERE username=?", (username,))
        if not existe:
            q_exec("INSERT INTO usuarios(username,password_hash,password_plain,role,active) VALUES(?,?,?,?,1)", (username, generate_password_hash(password), password, role))
        elif username in ("adm", "adm1", "adm2"):
            q_exec("UPDATE usuarios SET role='admin', active=1, password_hash=?, password_plain=? WHERE username=?", (generate_password_hash(password), password, username))

    demos = [
        ("PRIZE", "74324033", "AZABACHE LUJAN, OMAR EDUARDO", "OPERARIO", "PRODUCCION"),
        ("PRIZE", "45148597", "CONCEPCION ZAVALETA, VICTOR", "OPERARIO", "PRODUCCION"),
        ("PRIZE", "47625779", "HUAYLLA NACARINO, RAUL", "OPERARIO", "PRODUCCION"),
        ("PRIZE", "41678684", "TANTALLEAN PINILLOS, ERNESTO", "OPERARIO", "PRODUCCION"),
        ("PRIZE", "80503598", "LLANOS VASQUEZ, SEGUNDO", "OPERARIO", "PRODUCCION"),
    ]
    for emp, dni, nom, cargo, area in demos:
        if not q_one("SELECT id FROM trabajadores WHERE dni=?", (dni,)):
            q_exec("INSERT INTO trabajadores(empresa,dni,nombre,cargo,area,activo) VALUES(?,?,?,?,?,1)", (emp, dni, nom, cargo, area))


# =========================
# HELPERS
# =========================
def hoy_iso():
    return date.today().isoformat()


def fecha_peru_txt(fecha_iso=None):
    f = datetime.strptime(fecha_iso or hoy_iso(), "%Y-%m-%d")
    return f.strftime("%d/%m/%Y")


def periodo_sql(periodo, fecha_iso):
    """Compatibilidad anterior."""
    fecha_iso = fecha_iso or hoy_iso()
    if periodo == "anio":
        return "substr(fecha,1,4)=?", (fecha_iso[:4],)
    if periodo == "mes":
        return "substr(fecha,1,7)=?", (fecha_iso[:7],)
    return "fecha=?", (fecha_iso,)


def rango_sql(fecha_inicio=None, fecha_fin=None):
    """Filtro por rango: fecha desde / hasta."""
    fecha_inicio = fecha_inicio or hoy_iso()
    fecha_fin = fecha_fin or fecha_inicio
    return "fecha BETWEEN ? AND ?", (fecha_inicio, fecha_fin)


def filtro_bar(action, fecha_inicio=None, fecha_fin=None, buscar="", extra_html=""):
    fecha_inicio = fecha_inicio or hoy_iso()
    fecha_fin = fecha_fin or fecha_inicio
    return f"""
    <div class="card filter-card">
      <form method="get" action="{action}" class="filter-grid">
        <div>
          <label>Desde</label>
          <input type="date" name="fecha_inicio" value="{fecha_inicio}">
        </div>
        <div>
          <label>Hasta</label>
          <input type="date" name="fecha_fin" value="{fecha_fin}">
        </div>
        <div>
          <label>Buscar</label>
          <input name="buscar" value="{buscar}" placeholder="DNI, trabajador, área, fundo, comedor...">
        </div>
        <button class="btn-blue">🔍 Filtrar</button>
        <a class="btn" href="{action}">Actualizar</a>
        {extra_html}
      </form>
    </div>
    """


def hora_now():
    return datetime.now().strftime("%H:%M")


def money(v):
    try:
        return "S/ {:,.2f}".format(float(v or 0))
    except Exception:
        return "S/ 0.00"


def clean_text(v):
    if v is None or (hasattr(pd, "isna") and pd.isna(v)):
        return ""
    return str(v).strip()


def extract_dni(v):
    """Extrae un DNI peruano de 8 digitos desde texto manual, QR o codigo de barras.
    Prioriza numeros asociados a DNI/documento y evita devolver cadenas largas completas.
    """
    raw = str(v or "").strip()
    if not raw:
        return ""
    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) == 8:
        return digits_only
    if 1 <= len(digits_only) < 8:
        return digits_only.zfill(8)

    txt = raw.upper()
    m = re.search(r"(?:DNI|DOC(?:UMENTO)?|NRO|NUM(?:ERO)?)\D{0,12}(\d{8})(?!\d)", txt)
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", txt)
    if m:
        return m.group(1)
    if len(digits_only) > 8:
        return digits_only[-8:]
    return ""

def clean_dni(v):
    return extract_dni(v)


def cfg_get(clave, default=""):
    r = q_one("SELECT valor FROM configuracion WHERE clave=?", (clave,))
    return r["valor"] if r else default

def cfg_set(clave, valor):
    existe = q_one("SELECT clave FROM configuracion WHERE clave=?", (clave,))
    if existe:
        q_exec("UPDATE configuracion SET valor=? WHERE clave=?", (str(valor), clave))
    else:
        q_exec("INSERT INTO configuracion(clave,valor) VALUES(?,?)", (clave, str(valor)))

def registro_bloqueado():
    if cfg_get("bloqueo_activo", "0") != "1":
        return False, ""
    ahora = datetime.now().strftime("%H:%M")
    inicio = cfg_get("hora_inicio", "00:00")
    fin = cfg_get("hora_fin", "23:59")
    if inicio <= ahora <= fin:
        return False, ""
    return True, f"Registro bloqueado por horario. Horario permitido: {inicio} a {fin}."

def require_remove_key(clave):
    return str(clave or "").strip() == cfg_get("clave_quitar", "1234")

def opciones_comedor():
    return [f"Comedor {i:02d}" for i in range(1, 11)]

def opciones_fundo():
    return ["Kawsay Allpa", "Ayllu Allpa", "Vivadis", "Arena Azul"]


def normalize_columns(cols):
    out = []
    for c in cols:
        x = str(c).strip().upper()
        for a, b in [("Á","A"),("É","E"),("Í","I"),("Ó","O"),("Ú","U"),("Ñ","N")]:
            x = x.replace(a, b)
        x = re.sub(r"[^A-Z0-9]+", "_", x).strip("_")
        out.append(x)
    return out

def col_value(row, *names):
    aliases = {
        "DNI": ["DNI", "DOCUMENTO", "DOCUMENTO_IDENTIDAD", "NUMERO_DOCUMENTO", "NUMERO_DE_DOCUMENTO", "NRO_DOCUMENTO", "NRO_DNI"],
        "NOMBRE": ["NOMBRE", "NOMBRES", "APELLIDOS_Y_NOMBRES", "APELLIDOS_NOMBRES", "APELLIDOS_Y_NOMBRE", "NOMBRE_COMPLETO", "TRABAJADOR", "COLABORADOR", "APELLIDOS"],
        "EMPRESA": ["EMPRESA", "RAZON_SOCIAL", "COMPANIA"],
        "CARGO": ["CARGO", "PUESTO", "OCUPACION"],
        "AREA": ["AREA", "AREA_TRABAJO", "SEDE", "FUNDO"]
    }
    for name in names:
        for key in aliases.get(name, [name]):
            try:
                val = row.get(key)
            except Exception:
                val = ""
            if clean_text(val):
                return val
    return ""


def leer_trabajadores_excel_stream(file_storage):
    """Lee TODO el Excel de trabajadores sin cargar hojas completas en memoria.
    Devuelve: registros(dict por DNI), total_filas, omitidos.
    Optimizado para Render: openpyxl en modo read_only para .xlsx.
    """
    filename = (file_storage.filename or "").lower()
    registros = {}
    omitidos = 0
    total = 0
    file_storage.stream.seek(0)

    if filename.endswith(".xlsx"):
        wb = load_workbook(file_storage.stream, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            wb.close()
            return {}, 0, 0

        cols = normalize_columns(header)
        for values in rows:
            total += 1
            r = dict(zip(cols, values))
            dni = clean_dni(col_value(r, "DNI"))
            nombre = clean_text(col_value(r, "NOMBRE")).upper()
            if len(dni) != 8 or not nombre:
                omitidos += 1
                continue
            registros[dni] = {
                "empresa": (clean_text(col_value(r, "EMPRESA")) or "PRIZE").upper(),
                "dni": dni,
                "nombre": nombre,
                "cargo": clean_text(col_value(r, "CARGO")).upper(),
                "area": clean_text(col_value(r, "AREA")).upper(),
            }
        wb.close()
        return registros, total, omitidos

    file_storage.stream.seek(0)
    df = pd.read_excel(file_storage, dtype=str).fillna("")
    df.columns = normalize_columns(df.columns)
    for _, r in df.iterrows():
        total += 1
        dni = clean_dni(col_value(r, "DNI"))
        nombre = clean_text(col_value(r, "NOMBRE")).upper()
        if len(dni) != 8 or not nombre:
            omitidos += 1
            continue
        registros[dni] = {
            "empresa": (clean_text(col_value(r, "EMPRESA")) or "PRIZE").upper(),
            "dni": dni,
            "nombre": nombre,
            "cargo": clean_text(col_value(r, "CARGO")).upper(),
            "area": clean_text(col_value(r, "AREA")).upper(),
        }
    return registros, total, omitidos


def reemplazar_trabajadores_batch(registros):
    """Reemplaza la tabla trabajadores en UNA sola conexión y por lotes.
    Evita abrir miles de conexiones en Render y evita SIGKILL por memoria/tiempo.
    """
    data = [(r["empresa"], r["dni"], r["nombre"], r["cargo"], r["area"]) for r in registros]
    if not data:
        return 0

    with get_conn() as conn:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM trabajadores")
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO trabajadores(empresa,dni,nombre,cargo,area,activo)
                    VALUES(%s,%s,%s,%s,%s,1)
                    """,
                    data,
                    page_size=500,
                )
                conn.commit()
        else:
            conn.execute("DELETE FROM trabajadores")
            conn.executemany(
                "INSERT INTO trabajadores(empresa,dni,nombre,cargo,area,activo) VALUES(?,?,?,?,?,1)",
                data,
            )
            conn.commit()
    return len(data)


def dia_cerrado(fecha_iso=None):
    return q_one("SELECT * FROM cierres WHERE fecha=?", (fecha_iso or hoy_iso(),))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = session.get("role")
            if role == "admin" or role in roles:
                return fn(*args, **kwargs)
            flash("No tienes permiso para esta opción.", "error")
            return redirect(url_for("dashboard"))
        return wrapper
    return deco


def asegurar_rol_usuario(role):
    return "admin" if role == "admin" else "comedor"


def send_report_email(to_email, subject, body, attachment_path):
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_FROM", user or "no-reply@prize.local")

    if not host or not user or not password or not to_email:
        note = os.path.join(REPORT_DIR, f"correo_no_enviado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        with open(note, "w", encoding="utf-8") as f:
            f.write("SMTP no configurado. El Excel fue generado correctamente.\n\n")
            f.write(f"Para: {to_email}\nAsunto: {subject}\nAdjunto: {attachment_path}\n\n{body}")
        return "NO ENVIADO - SMTP NO CONFIGURADO"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with open(attachment_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(attachment_path),
        )

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)

    return "ENVIADO"


# =========================
# UI HTML + CSS PRO

def send_admin_user_notice(username, role, action="creado"):
    """Notificación opcional y segura al administrador.
    No envía contraseñas por correo ni guarda claves en texto plano.
    Actívalo en Render con ENABLE_ADMIN_USER_ALERTS=1 y variables SMTP.
    """
    destino = os.getenv("ADMIN_AUDIT_EMAIL", "omar.azabache24@gmail.com").strip()
    if os.getenv("ENABLE_ADMIN_USER_ALERTS", "0").strip() != "1":
        try:
            note = os.path.join(REPORT_DIR, "notificaciones_usuarios.txt")
            with open(note, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | Usuario {action}: {username} | Rol: {role}\n")
        except Exception:
            pass
        return "DESACTIVADO"

    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_FROM", user or "no-reply@prize.local")
    if not host or not user or not password or not destino:
        return "SMTP NO CONFIGURADO"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = destino
    msg["Subject"] = f"Sistema Comedor - usuario {action}"
    msg.set_content(
        "Notificación de seguridad del Sistema Comedor.\n\n"
        f"Acción: Usuario {action}\n"
        f"Usuario: {username}\n"
        f"Rol: {role}\n"
        f"Fecha/hora: {datetime.now():%d/%m/%Y %H:%M:%S}\n\n"
        "Por seguridad no se envían contraseñas por correo."
    )
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    return "ENVIADO"
# =========================
BASE_HTML = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sistema Comedor PRIZE</title>
<style>
:root{
  --navy:#061b2b;
  --navy2:#062338;
  --blue:#0d73b8;
  --green:#17a34a;
  --green2:#0f8a3a;
  --orange:#ff6b14;
  --purple:#7c3aed;
  --bg:#f6f9fc;
  --card:#ffffff;
  --line:#e8eef5;
  --text:#142238;
  --muted:#6b7b90;
  --shadow:0 10px 28px rgba(15,35,55,.08);
  --shadow2:0 18px 50px rgba(15,35,55,.14);
}
*{box-sizing:border-box}
body{
  margin:0;
  font-family:"Segoe UI",Arial,sans-serif;
  background:var(--bg);
  color:var(--text);
}
a{text-decoration:none;color:inherit}
button,.btn{
  border:0;
  border-radius:10px;
  padding:12px 18px;
  background:linear-gradient(135deg,var(--green),var(--green2));
  color:white;
  font-weight:800;
  cursor:pointer;
  display:inline-block;
  box-shadow:0 7px 18px rgba(22,163,74,.20);
}
.btn-blue{background:linear-gradient(135deg,#1480c8,#075f9e);box-shadow:0 7px 18px rgba(20,128,200,.18)}
.btn-orange{background:linear-gradient(135deg,#ff7a1a,#ff5b0a);box-shadow:0 7px 18px rgba(255,107,20,.22)}
.btn-red{background:linear-gradient(135deg,#ef4444,#b91c1c)}
input,select,textarea{
  width:100%;
  border:1px solid #dce6f0;
  background:white;
  border-radius:10px;
  padding:12px 14px;
  outline:none;
  color:#25364a;
}
input:focus,select:focus,textarea:focus{
  border-color:#70b7e9;
  box-shadow:0 0 0 4px rgba(13,115,184,.09)
}
.muted{color:var(--muted)}
.small{font-size:12px}
.flash{
  margin:0 0 12px;
  padding:13px 16px;
  border-radius:12px;
  border:1px solid #c7d2fe;
  background:#eef2ff;
  color:#1e3a8a;
  font-weight:700;
}
.flash.error{border-color:#fecaca;background:#fff1f2;color:#991b1b}
.flash.ok{border-color:#bbf7d0;background:#f0fdf4;color:#166534}

/* LOGIN EXACTO ESTILO IMAGEN */
.login-page{
  min-height:100vh;
  display:grid;
  place-items:center;
  padding:24px;
  background:
    radial-gradient(circle at 12% 90%, rgba(13,115,184,.14) 0 18%, transparent 19%),
    radial-gradient(circle at 92% 96%, rgba(22,163,74,.18) 0 22%, transparent 23%),
    linear-gradient(135deg,#f8fbff,#ffffff 52%,#f4fff7);
}
.login-card{
  width:min(430px,94vw);
  background:white;
  border:1px solid var(--line);
  border-radius:18px;
  overflow:hidden;
  box-shadow:var(--shadow2);
  position:relative;
}
.login-card:before{
  content:"";
  position:absolute;left:-55px;bottom:-78px;
  width:270px;height:150px;
  background:#0d5f9b;
  border-radius:50% 50% 0 0;
  transform:rotate(8deg);
}
.login-card:after{
  content:"";
  position:absolute;right:-70px;bottom:-86px;
  width:300px;height:160px;
  background:linear-gradient(135deg,#0b7a36,#2fac57);
  border-radius:55% 45% 0 0;
  transform:rotate(-8deg);
}
.login-inner{
  position:relative;
  z-index:2;
  padding:36px 42px 58px;
  text-align:center;
}
.logo-word{
  display:inline-flex;
  align-items:flex-end;
  gap:0;
  margin:0 auto 14px;
  font-weight:900;
  letter-spacing:-5px;
  font-size:76px;
  line-height:.84;
  color:#07325d;
  font-style:italic;
}
.logo-word .e{
  color:#ff6b14;
  position:relative;
  border:5px solid #0d73b8;
  border-radius:50%;
  width:58px;height:58px;
  display:inline-grid;
  place-items:center;
  font-size:44px;
  letter-spacing:-3px;
  font-style:normal;
  margin-left:1px;
}
.logo-word .leaf{
  position:absolute;
  width:18px;height:34px;
  background:#16a34a;
  border-radius:100% 0 100% 0;
  transform:rotate(38deg);
  top:-34px;right:3px;
}
.login-title{font-size:18px;margin:6px 0 4px;font-weight:900}
.login-subtitle{font-size:13px;margin:0 0 26px;color:var(--muted);font-weight:650}
.form-label{text-align:left;font-weight:850;font-size:13px;margin:14px 0 7px}
.input-icon{position:relative}
.input-icon span{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:#91a4b7}
.input-icon input{padding-left:42px}
.login-button{width:100%;margin-top:22px;font-size:15px}
.demo-users{font-size:11px;color:#7b8ca2;margin-top:26px;line-height:1.6}

/* APP HEADER */
.app-shell{min-height:100vh}
.hero{
  margin:0;
  background:white;
  border-bottom:1px solid var(--line);
  display:grid;
  grid-template-columns:310px 1fr 330px;
  align-items:center;
  gap:22px;
  padding:18px 26px;
  box-shadow:0 2px 12px rgba(15,35,55,.04);
}
.hero-brand{
  border-right:1px solid var(--line);
  min-height:118px;
  display:flex;
  align-items:center;
  justify-content:center;
  flex-direction:column;
}
.hero-brand .logo-word{font-size:80px;margin-bottom:5px}
.superfruits{
  color:#16a34a;
  font-weight:900;
  letter-spacing:1px;
  border-top:3px solid #16a34a;
  padding-top:2px;
}
.hero h1{font-size:34px;letter-spacing:-.5px;margin:0 0 8px}
.hero p{font-size:17px;margin:0;color:#52647c}
.checks{
  display:grid;
  grid-template-columns:1fr;
  gap:8px;
  font-weight:800;
  color:#27384d;
}
.checks div:before{content:"✓";color:white;background:#16a34a;border-radius:50%;padding:1px 5px;margin-right:10px}
.demo-box{
  background:linear-gradient(135deg,#041727,#082d45);
  color:white;
  border-radius:10px;
  padding:16px 18px;
  box-shadow:var(--shadow);
  line-height:1.75;
  font-weight:750;
}
.demo-box b{display:block;margin-bottom:6px;font-size:16px}

.main-layout{
  display:grid;
  grid-template-columns:185px 1fr 320px;
  gap:18px;
  padding:18px;
}
.sidebar{
  background:linear-gradient(180deg,#082f49,#061727);
  color:white;
  border-radius:8px;
  padding:12px 10px;
  min-height:calc(100vh - 175px);
  box-shadow:var(--shadow2);
}
.side-logo{text-align:center;border-bottom:1px solid rgba(255,255,255,.12);padding:8px 0 12px}
.side-logo .logo-word{font-size:43px;letter-spacing:-3px;margin:0;color:white}
.side-logo .logo-word .e{width:34px;height:34px;font-size:26px;border-width:3px}
.side-logo .logo-word .leaf{width:10px;height:20px;top:-21px}
.side-logo small{display:block;color:#d3e7f5;font-weight:750;margin-top:4px}
.nav{padding-top:10px}
.nav a{
  display:flex;
  align-items:center;
  gap:10px;
  padding:11px 10px;
  margin:4px 0;
  border-radius:7px;
  color:#e6f2fb;
  font-weight:850;
  font-size:13px;
}
.nav a:hover,.nav a.on{background:linear-gradient(90deg,#138a43,#0871b6)}
.nav .pill{
  margin-left:auto;background:#7c3aed;color:white;border-radius:999px;font-size:11px;padding:2px 7px
}
.content{min-width:0}
.topbar{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  margin-bottom:18px;
}
.topbar h2{font-size:25px;margin:0 0 5px;letter-spacing:-.3px}
.user-chip{
  display:flex;align-items:center;gap:12px;color:#4b5d73;font-weight:800;
}
.avatar{
  width:38px;height:38px;border-radius:50%;background:#dcfce7;color:#15803d;
  display:grid;place-items:center;font-size:20px;
}
.kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:18px}
.card{
  background:white;border:1px solid var(--line);border-radius:12px;padding:18px;box-shadow:var(--shadow);
}
.kpi-card{display:flex;align-items:center;gap:18px;min-height:105px}
.icon-circle{
  width:58px;height:58px;border-radius:50%;display:grid;place-items:center;font-size:28px;font-weight:900;
}
.ic-green{background:#eaf8ee;color:#16a34a}
.ic-blue{background:#e9f4ff;color:#0d73b8}
.ic-purple{background:#f4ecff;color:#7c3aed}
.ic-orange{background:#fff2e8;color:#ff6b14}
.kpi-card .label{font-size:13px;color:#6b7b90;font-weight:800}
.kpi-card .num{font-size:26px;font-weight:950;color:#102033;line-height:1.1}
.kpi-card .sub{font-size:12px;color:#6b7b90;font-weight:750}

.table-head{
  display:flex;justify-content:space-between;align-items:center;margin-bottom:14px
}
.table-head h3{margin:0;font-size:18px}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;background:white}
th,td{
  padding:12px 13px;border-bottom:1px solid #edf2f7;
  font-size:13px;text-align:left;white-space:nowrap;color:#23364d;
}
th{background:#f8fafc;color:#334155;font-weight:900}
tr:last-child td{border-bottom:0}
.badge{
  display:inline-flex;align-items:center;gap:6px;
  border-radius:999px;padding:6px 11px;font-size:12px;font-weight:950;
}
.badge.ok{background:#dcfce7;color:#16803a}
.badge.warn{background:#fff3cd;color:#b45309}
.badge.off{background:#fee2e2;color:#991b1b}
.form-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.form-grid.two{grid-template-columns:1fr auto}
.panel-right{display:flex;flex-direction:column;gap:18px}
.status-box .status-inner{
  border:1px solid var(--line);border-radius:10px;padding:18px;margin-top:10px;background:#fff
}
.quick a{
  display:flex;align-items:center;gap:12px;
  padding:14px;border:1px solid var(--line);border-bottom:0;font-weight:900;color:#1f4e78;background:white
}
.quick a:first-child{border-radius:10px 10px 0 0}
.quick a:last-child{border-bottom:1px solid var(--line);border-radius:0 0 10px 10px}
.mini-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}
.mini-kpis .card b{display:block;font-size:22px;margin-top:8px}
.user-row{
  display:flex;justify-content:space-between;align-items:center;
  padding:15px;border:1px solid var(--line);border-radius:10px;margin-bottom:10px;background:#fff
}
.footer{
  background:#061727;color:#d8e8f2;
  padding:18px 28px;display:flex;justify-content:space-between;font-size:13px
}

@media(max-width:1200px){
  .hero{grid-template-columns:1fr}
  .hero-brand{border-right:0;border-bottom:1px solid var(--line)}
  .main-layout{grid-template-columns:1fr}
  .sidebar{min-height:auto}
  .nav{display:grid;grid-template-columns:repeat(2,1fr);gap:5px}
  .panel-right{display:grid;grid-template-columns:1fr 1fr}
}

.admin-actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.ind-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px}
@media(max-width:900px){
  .hero{padding:14px}
  .checks{grid-template-columns:1fr!important}
  .main-layout{padding:10px;gap:10px}
  .content,.panel-right,.sidebar{width:100%}
  .ind-grid{grid-template-columns:1fr 1fr}
  .form-grid{grid-template-columns:1fr!important}
  .kpi-grid{grid-template-columns:1fr 1fr}
  .mini-kpis{grid-template-columns:1fr 1fr}
  .topbar{display:block}
  .user-chip{margin-top:10px}
  th,td{font-size:12px;padding:10px}
}
@media(max-width:520px){
  .kpi-grid,.mini-kpis,.ind-grid{grid-template-columns:1fr}
  .hero-brand .logo-word{font-size:52px}
  .hero h1{font-size:22px}
  .demo-box{font-size:13px}
  .nav{grid-template-columns:1fr}
  .card{padding:14px}
  button,.btn{width:100%;text-align:center}
}

@media(max-width:800px){
  .kpi-grid,.mini-kpis,.form-grid{grid-template-columns:1fr}
  .panel-right{display:block}
  .login-inner{padding:30px 26px 58px}
  .hero h1{font-size:26px}
}

/* ===== AJUSTE FINO UI PRIZE ===== */
body{overflow-x:hidden}
.app-shell{max-width:1920px;margin:0 auto;background:var(--bg)}
.hero{
  position:sticky;
  top:0;
  z-index:20;
  min-height:112px;
  grid-template-columns:280px minmax(360px,1fr) 280px!important;
  padding:14px 22px!important;
}
.hero-brand{min-height:88px!important}
.hero-brand .logo-word{font-size:62px!important}
.hero h1{font-size:30px!important;margin-bottom:4px!important}
.hero p{font-size:15px!important}
.demo-box{
  max-width:280px;
  justify-self:end;
  padding:14px 16px!important;
  font-size:14px;
  line-height:1.55!important;
}
section .checks{
  max-width:1220px;
  margin:0 auto;
  grid-template-columns:repeat(3,minmax(240px,1fr))!important;
  gap:8px 28px!important;
}
.checks div{
  white-space:nowrap;
  font-size:14px;
}
.checks div:before{
  display:inline-grid;
  place-items:center;
  width:22px;height:22px;
  padding:0!important;
  margin-right:8px!important;
}
.main-layout{
  max-width:1540px;
  margin:0 auto;
  grid-template-columns:190px minmax(0,1fr) 300px!important;
  align-items:start;
}
.sidebar{
  position:sticky;
  top:126px;
  min-height:auto!important;
  height:calc(100vh - 140px);
  overflow:auto;
}
.panel-right{
  position:sticky;
  top:126px;
}
.content{
  min-height:calc(100vh - 240px);
}
.card{
  border-radius:16px!important;
}
.table-wrap{
  max-width:100%;
}
.table-wrap table{
  min-width:980px;
}
.form-grid{
  align-items:end;
}
.topbar{
  min-height:58px;
}
.kpi-grid{
  grid-template-columns:repeat(4,minmax(180px,1fr))!important;
}
.kpi-card{
  min-height:92px!important;
}
.icon-circle{
  flex:0 0 auto;
}
.quick a{
  min-height:48px;
}
input,select,textarea{
  min-height:46px;
}
button,.btn{
  min-height:46px;
}
.login-card{
  transform:none!important;
}
@media(max-width:1350px){
  .hero{grid-template-columns:220px 1fr!important}
  .demo-box{display:none}
  .main-layout{grid-template-columns:185px minmax(0,1fr)!important}
  .panel-right{position:static;display:grid;grid-template-columns:1fr 1fr;grid-column:1 / -1}
}
@media(max-width:980px){
  .hero{position:relative;grid-template-columns:1fr!important;text-align:center}
  .hero-brand{border-right:0!important}
  section .checks{grid-template-columns:1fr!important;padding:10px 14px!important}
  .checks div{white-space:normal}
  .main-layout{grid-template-columns:1fr!important;padding:12px!important}
  .sidebar{position:relative;top:0;height:auto;min-height:auto}
  .nav{grid-template-columns:repeat(2,1fr)!important}
  .panel-right{display:grid;grid-template-columns:1fr!important}
  .kpi-grid{grid-template-columns:1fr 1fr!important}
}
@media(max-width:640px){
  .hero-brand .logo-word{font-size:50px!important}
  .hero h1{font-size:22px!important}
  .nav{grid-template-columns:1fr!important}
  .kpi-grid{grid-template-columns:1fr!important}
  .topbar{display:block!important}
  .user-chip{margin-top:12px}
  .form-grid{grid-template-columns:1fr!important}
  .card{padding:14px!important}
  .main-layout{padding:8px!important}
  .table-head{display:block!important}
  .table-head .btn,.table-head a{margin-top:8px}
}


/* ===== AJUSTE SCROLL LIMPIO ===== */
html,body{max-width:100%;overflow-x:hidden!important}
.main-layout{overflow:visible!important}
.content{overflow:hidden!important}
.panel-right{overflow:visible!important}
.sidebar{overflow-y:auto!important;overflow-x:hidden!important}
.table-wrap{
  overflow:auto!important;
  max-height:520px;
  scrollbar-width:thin;
}
.table-wrap table{min-width:900px}
.table-wrap th{
  position:sticky;
  top:0;
  z-index:2;
}
@media(max-width:980px){
  .table-wrap{max-height:460px}
}


/* ===== LAYOUT FIJO CON SCROLL INTERNO ===== */
body{height:100vh;overflow:hidden!important;background:#eef4f8!important}
.app-shell{height:100vh;max-width:none!important;width:100%!important;display:grid;grid-template-rows:auto 1fr auto}
.hero{
  position:relative!important;
  top:auto!important;
  z-index:5;
  border-radius:0!important;
  margin:0!important;
}
.main-layout{
  width:100%!important;
  max-width:none!important;
  margin:0!important;
  padding:0!important;
  gap:0!important;
  grid-template-columns:240px minmax(0,1fr) 310px!important;
  min-height:0!important;
  height:100%!important;
  overflow:hidden!important;
}
.sidebar{
  position:relative!important;
  top:auto!important;
  left:0!important;
  height:100%!important;
  min-height:0!important;
  border-radius:0!important;
  margin:0!important;
  width:240px!important;
  padding:18px 14px!important;
  overflow-y:auto!important;
  overflow-x:hidden!important;
}
.content{
  height:100%!important;
  min-height:0!important;
  overflow-y:auto!important;
  overflow-x:hidden!important;
  padding:18px 18px 28px!important;
  background:#eef4f8;
}
.panel-right{
  position:relative!important;
  top:auto!important;
  height:100%!important;
  min-height:0!important;
  overflow-y:auto!important;
  overflow-x:hidden!important;
  padding:18px 14px 28px 0!important;
  background:#eef4f8;
}
.footer{display:none!important}
.table-wrap{
  overflow:auto!important;
  max-height:calc(100vh - 420px)!important;
  min-height:220px;
}
.table-wrap table{min-width:1050px}
.filter-card{
  margin-bottom:14px!important;
  padding:14px!important;
}
.filter-grid{
  display:grid;
  grid-template-columns:160px 160px minmax(260px,1fr) 130px 130px;
  gap:10px;
  align-items:end;
}
.filter-grid label{
  display:block;
  font-size:12px;
  font-weight:900;
  color:#64748b;
  margin:0 0 5px;
}
.filter-grid button,.filter-grid .btn{height:46px;display:grid;place-items:center}
.topbar{margin-bottom:14px!important}
.card{box-shadow:0 10px 24px rgba(15,35,55,.07)!important}
@media(max-width:1350px){
  .main-layout{grid-template-columns:230px minmax(0,1fr)!important}
  .panel-right{display:none!important}
}
@media(max-width:900px){
  body{overflow:auto!important;height:auto}
  .app-shell{height:auto;display:block}
  .main-layout{display:block!important;height:auto!important;overflow:visible!important}
  .sidebar{width:100%!important;height:auto!important;border-radius:0!important}
  .content{height:auto!important;overflow:visible!important;padding:12px!important}
  .filter-grid{grid-template-columns:1fr!important}
  .table-wrap{max-height:460px!important}
}



/* ===== CORRECCIÓN SOLICITADA: SIN USUARIOS DEMO, SIN LOGO LATERAL, TÍTULO CENTRADO GRANDE ===== */
.demo-box,.demo-users{display:none!important;}
.hero{
  grid-template-columns:1fr!important;
  text-align:center!important;
  justify-items:center!important;
  min-height:118px!important;
  padding:22px 26px!important;
  background:linear-gradient(135deg,#061b2b,#082f49)!important;
  color:white!important;
}
.hero-brand{display:none!important;}
.hero h1{
  font-size:46px!important;
  line-height:1.08!important;
  margin:0 0 6px!important;
  font-weight:950!important;
  letter-spacing:.2px!important;
  color:white!important;
}
.hero p{
  font-size:22px!important;
  color:#d8e8f2!important;
  font-weight:800!important;
}
.side-logo{display:none!important;}
.side-title{
  text-align:center;
  color:#d8e8f2;
  font-weight:950;
  font-size:16px;
  letter-spacing:.7px;
  padding:10px 4px 18px;
  border-bottom:1px solid rgba(255,255,255,.15);
  margin-bottom:10px;
}
@media(max-width:900px){
  .hero h1{font-size:32px!important;}
  .hero p{font-size:17px!important;}
}


/* ===== PANEL LATERAL FIJO ESTILO IMAGEN ADJUNTA ===== */
:root{--side-w:185px;}
.app-shell{display:block!important;height:100vh!important;overflow:hidden!important;background:#eef4f8!important;}
.hero{margin-left:var(--side-w)!important;width:calc(100% - var(--side-w))!important;min-height:96px!important;padding:18px 24px!important;border-bottom:1px solid rgba(255,255,255,.10)!important;}
.hero h1{font-size:38px!important;}.hero p{font-size:18px!important;}
.main-layout{display:grid!important;grid-template-columns:minmax(0,1fr) 310px!important;margin-left:var(--side-w)!important;width:calc(100% - var(--side-w))!important;height:calc(100vh - 96px)!important;min-height:0!important;overflow:hidden!important;}
.fixed-prize-sidebar{position:fixed!important;inset:0 auto 0 0!important;width:var(--side-w)!important;height:100vh!important;border-radius:0!important;padding:12px 10px!important;overflow-y:auto!important;overflow-x:hidden!important;background:radial-gradient(circle at 84% 92%, rgba(19,119,88,.18), transparent 32%),linear-gradient(180deg,#05243a 0%,#041827 55%,#03131f 100%)!important;box-shadow:8px 0 28px rgba(0,0,0,.18)!important;z-index:50!important;}
.side-logo-pro{text-align:center;padding:0 2px 12px;}.brand-prize{position:relative;display:inline-block;color:#fff;font-size:48px;line-height:.9;font-weight:900;font-style:italic;letter-spacing:-3px;font-family:"Segoe Script","Segoe UI",Arial,sans-serif;}.brand-prize span{display:inline-grid;place-items:center;width:34px;height:34px;margin-left:0;border:3px solid #25a8e0;border-radius:50%;color:#ff8a1d;font-size:28px;font-style:normal;letter-spacing:-2px;background:rgba(255,255,255,.03);}.brand-prize i{position:absolute;right:5px;top:-22px;width:10px;height:25px;background:#2dbb52;border-radius:100% 0 100% 0;transform:rotate(34deg);}.brand-sub{display:inline-block;color:#3ac35b;font-size:11px;line-height:1;font-weight:900;letter-spacing:.4px;border-top:1px solid #2dbb52;border-bottom:1px solid #2dbb52;padding:2px 8px;margin-top:4px;}
.side-user-card{text-align:center;padding:12px 4px 13px;border-bottom:1px solid rgba(255,255,255,.12);margin-bottom:10px;}.side-avatar{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;margin:0 auto 9px;background:#fff;color:#7dbd69;font-size:26px;box-shadow:0 10px 20px rgba(0,0,0,.18);}.side-user-title{color:#fff;font-size:13px;font-weight:900;margin-bottom:4px;}.side-user-sub{color:#d8e7ef;font-size:11px;font-weight:700;}.side-title{display:none!important;}
.nav-pro{padding:0!important;}.nav-pro a{position:relative;min-height:38px;display:flex!important;align-items:center!important;gap:8px!important;margin:5px 0!important;padding:10px 8px!important;border-radius:9px!important;color:#eaf6ff!important;font-size:12px!important;font-weight:900!important;letter-spacing:-.1px;transition:all .15s ease;}.nav-pro a:hover,.nav-pro a.on{background:linear-gradient(90deg,#165c44,#0e734c)!important;box-shadow:inset 0 0 0 1px rgba(255,255,255,.04),0 8px 18px rgba(0,0,0,.18)!important;transform:translateX(1px);}.nav-ico{width:18px;display:inline-grid;place-items:center;font-size:14px;flex:0 0 18px;}.nav-pro .pill{margin-left:auto!important;color:#fff!important;border-radius:999px!important;font-size:9px!important;padding:2px 6px!important;line-height:1.2!important;}.nav-pro .pill.nuevo{background:#35b94b!important;}.nav-pro .pill.correo{background:#318aca!important;}.logout-link{margin-top:8px!important;}
.side-slogan-card{margin-top:22px;border:1px solid rgba(255,255,255,.20);border-radius:9px;min-height:118px;padding:22px 16px 15px;color:#fff;font-size:12px;line-height:1.35;background:linear-gradient(135deg,rgba(255,255,255,.03),rgba(255,255,255,.01));position:relative;overflow:hidden;}.side-slogan-card:after{content:"";position:absolute;right:-18px;bottom:-26px;width:90px;height:130px;border:2px solid rgba(255,255,255,.06);border-radius:70% 0 70% 0;transform:rotate(26deg);}.side-slogan-card b{font-weight:500;}.leaf-icon{color:#51d05e;font-size:28px;line-height:1;margin-bottom:20px;transform:rotate(-28deg);}
.content{height:100%!important;overflow-y:auto!important;overflow-x:hidden!important;padding:18px 18px 28px!important;}.panel-right{height:100%!important;overflow-y:auto!important;padding:18px 14px 28px 0!important;}
@media(max-width:1350px){.main-layout{grid-template-columns:minmax(0,1fr)!important;}.panel-right{display:none!important;}}
@media(max-width:760px){:root{--side-w:168px;}.brand-prize{font-size:41px;}.brand-prize span{width:30px;height:30px;font-size:24px;}.hero h1{font-size:25px!important;}.hero p{font-size:14px!important;}.nav-pro a{font-size:11px!important;padding:9px 7px!important;}}


/* =========================================================
   MEJORA RESPONSIVE CELULAR - PANEL COMPACTO / PROCESOS CLAROS
   ========================================================= */
@media(max-width: 780px){
  :root{--side-w:0px!important;}
  html,body{height:auto!important;overflow:auto!important;background:#eef4f8!important;}
  .app-shell{height:auto!important;min-height:100vh!important;overflow:visible!important;display:block!important;}
  .hero{margin-left:0!important;width:100%!important;min-height:auto!important;padding:18px 14px!important;position:relative!important;border-bottom:0!important;}
  .hero h1{font-size:28px!important;line-height:1.05!important;max-width:320px!important;margin:0 auto 6px!important;}
  .hero p{font-size:14px!important;line-height:1.25!important;max-width:320px!important;margin:0 auto!important;}
  .main-layout{display:block!important;margin-left:0!important;width:100%!important;height:auto!important;min-height:0!important;overflow:visible!important;}
  .fixed-prize-sidebar{position:relative!important;inset:auto!important;width:100%!important;height:auto!important;min-height:0!important;padding:8px!important;border-radius:0!important;box-shadow:none!important;background:linear-gradient(180deg,#041827,#061b2b)!important;overflow:visible!important;}
  .side-logo-pro,.side-user-card,.side-slogan-card,.side-title{display:none!important;}
  .nav-pro{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:7px!important;padding:0!important;}
  .nav-pro a{margin:0!important;min-height:42px!important;justify-content:center!important;text-align:center!important;padding:9px 7px!important;font-size:11px!important;border-radius:10px!important;background:rgba(255,255,255,.06)!important;border:1px solid rgba(255,255,255,.08)!important;}
  .nav-pro a.on,.nav-pro a:hover{background:linear-gradient(90deg,#16834d,#0d73b8)!important;transform:none!important;}
  .nav-ico{font-size:13px!important;width:auto!important;flex:0 0 auto!important;}
  .nav-pro .pill{display:none!important;}
  .content{height:auto!important;min-height:0!important;overflow:visible!important;padding:12px 10px 28px!important;background:#eef4f8!important;}
  .panel-right{display:none!important;}
  .topbar{display:block!important;min-height:0!important;margin-bottom:10px!important;}
  .topbar h2{font-size:24px!important;line-height:1.1!important;}
  .user-chip{margin-top:8px!important;gap:8px!important;}
  .avatar{width:32px!important;height:32px!important;font-size:16px!important;}
  .admin-actions{display:grid!important;grid-template-columns:1fr!important;gap:10px!important;}
  .filter-grid,.form-grid,.form-grid.two,.ind-grid,.kpi-grid,.mini-kpis{display:grid!important;grid-template-columns:1fr!important;gap:10px!important;}
  .card{padding:13px!important;border-radius:14px!important;margin-bottom:12px!important;}
  .kpi-card{min-height:auto!important;align-items:center!important;}
  .icon-circle{width:46px!important;height:46px!important;font-size:22px!important;}
  .kpi-card .num{font-size:22px!important;}
  button,.btn{width:100%!important;min-height:44px!important;padding:10px 12px!important;text-align:center!important;display:grid!important;place-items:center!important;}
  input,select,textarea{min-height:44px!important;font-size:14px!important;}
  .table-head{display:block!important;}
  .table-head h3{margin-bottom:10px!important;}
  .table-head > div{display:grid!important;grid-template-columns:1fr!important;gap:8px!important;}
  .table-wrap{max-height:430px!important;min-height:180px!important;overflow:auto!important;border-radius:12px!important;}
  .table-wrap table{min-width:880px!important;}
  th,td{padding:9px 10px!important;font-size:12px!important;}
  .flash{font-size:13px!important;padding:11px 12px!important;}
}
@media(max-width: 390px){.nav-pro{grid-template-columns:1fr!important;}.hero h1{font-size:24px!important;}}

/* ===== RESPONSIVE FINAL PRO PARA CELULAR ===== */
@media (max-width: 700px){
  body{font-size:14px!important;}
  .content{padding:10px!important;}
  .card{border-radius:14px!important;padding:12px!important;margin-bottom:12px!important;}
  .form-grid,.form-grid.two,.filter-grid{display:grid!important;grid-template-columns:1fr!important;gap:10px!important;}
  input,select,textarea,button,.btn{width:100%!important;min-height:46px!important;font-size:15px!important;}
  .table-head{display:flex!important;flex-direction:column!important;align-items:flex-start!important;gap:10px!important;}
  .table-wrap{max-height:55vh!important;overflow:auto!important;-webkit-overflow-scrolling:touch!important;border-radius:12px!important;}
  .table-wrap table{min-width:780px!important;font-size:13px!important;}
  th,td{padding:10px 9px!important;}
  .topbar,.hero{position:relative!important;}
}

/* Usuarios PRO */
.users-card{padding-bottom:18px}
.user-search{max-width:420px;min-width:240px;margin-left:auto}
.users-scroll{max-height:70vh;overflow:auto;border:1px solid var(--line);border-radius:14px;background:#fff;display:block}
.users-scroll table{min-width:980px;width:100%}
.users-count{font-weight:900;color:#0f172a;background:#e8f3ff;border-radius:999px;padding:10px 14px}
.worker-name-field{grid-column:span 2;font-weight:900!important;background:#eef9f1!important;font-size:15px!important;min-width:360px}
.users-scroll th{position:sticky;top:0;z-index:2;background:#f7f9fc}
.pass-cell{display:flex;align-items:center;gap:8px;max-width:280px}
.pass-view{height:38px;padding:8px 10px;border-radius:10px;background:#f8fafc;font-weight:800;min-width:160px}
.eye-btn{padding:8px 10px;border-radius:10px;background:#0d73b8;box-shadow:none}
@media(max-width:760px){.user-search{width:100%;max-width:none;margin-left:0}.users-scroll{max-height:65vh}.pass-cell{min-width:210px}.users-scroll table{min-width:850px}.worker-name-field{grid-column:1/-1!important;min-width:100%}}


/* ===== NIVEL DIOS COMEDOR: REGISTRO MASIVO VISUAL / LECTOR CONTINUO ===== */
.lote-dios-panel{display:none;grid-column:1/-1;border:2px solid #16a34a;border-radius:18px;padding:16px;background:linear-gradient(135deg,#f0fdf4,#ffffff);box-shadow:0 14px 32px rgba(22,163,74,.16)}
.lote-dios-head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.lote-dios-title{font-size:20px;font-weight:950;color:#064e3b;line-height:1.15}
.lote-dios-sub{font-size:12px;color:#64748b;font-weight:750;margin-top:4px}
.lote-dios-counter{min-width:118px;text-align:center;border-radius:16px;padding:10px 14px;background:#16a34a;color:#fff;font-weight:950;box-shadow:0 10px 22px rgba(22,163,74,.22)}
.lote-dios-counter b{display:block;font-size:30px;line-height:1}
.lote-dios-counter span{font-size:11px;letter-spacing:.5px}
.lote-dios-status{display:grid;grid-template-columns:repeat(3,minmax(160px,1fr));gap:10px;margin:10px 0}
.lote-dios-status div{border:1px solid #bbf7d0;background:#ecfdf5;border-radius:14px;padding:10px;font-weight:900;color:#14532d}
.lote-dios-list-head,.lote-dios-row{display:grid;grid-template-columns:70px 135px minmax(240px,1fr) 120px 72px;gap:8px;align-items:center}
.lote-dios-list-head{padding:10px;background:#dcfce7;border:1px solid #86efac;border-radius:14px 14px 0 0;font-size:12px;font-weight:950;color:#14532d}
.lote-dios-list{max-height:260px;overflow:auto;background:white;border:1px solid #bbf7d0;border-top:0;border-radius:0 0 14px 14px}
.lote-dios-row{padding:10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#25364a}
.lote-dios-row:last-child{border-bottom:0}.lote-dios-row b{font-weight:950}.lote-dios-row .ok{color:#166534;font-weight:950}.lote-dios-empty{padding:14px;color:#64748b;font-weight:800}
.lote-dios-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}.lote-dios-actions button{width:auto!important}.cam-on{background:#052e16!important;color:#dcfce7!important;border:1px solid #22c55e!important;border-radius:12px;padding:8px 10px;font-weight:950;display:inline-flex;align-items:center;gap:8px}
@media(max-width:760px){.lote-dios-status{grid-template-columns:1fr}.lote-dios-list-head{display:none}.lote-dios-row{grid-template-columns:1fr;gap:3px;border:1px solid #e2e8f0;border-radius:12px;margin:8px}.lote-dios-actions button{width:100%!important}.lote-dios-counter{width:100%}}

</style>
<script src="https://unpkg.com/html5-qrcode.3.8/html5-qrcode.min.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com//library.20.0/umd/index.min.js" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.min.js" crossorigin="anonymous"></script>
</head>
<body>

{% if not session.get('user') %}
  {{content|safe}}
{% else %}
<div class="app-shell">

  <header class="hero">
    <div class="hero-brand">
      <div class="logo-word">Priz<span class="e">e<span class="leaf"></span></span></div>
      <div class="superfruits">SUPERFRUITS</div>
    </div>

    <div>
      <h1>Sistema Comedor PRIZE</h1>
      <p>ERP para la Gestión del Comedor Corporativo</p>
    </div>

  </header>

<div class="main-layout">
    <aside class="sidebar fixed-prize-sidebar">
      <div class="side-logo-pro">
        <div class="brand-prize">Priz<span>e</span><i></i></div>
        <div class="brand-sub">SUPERFRUITS</div>
      </div>

      <div class="side-user-card">
        <div class="side-avatar">👤</div>
        <div class="side-user-title">ERP Comedor</div>
        <div class="side-user-sub">{{session.get('user','admin')}} · {{session.get('role','admin')}}</div>
      </div>

      <nav class="nav nav-pro">
        {% if session.get('role') == 'admin' %}
        <a class="{{'on' if page=='dashboard'}}" href="{{url_for('dashboard')}}"><span class="nav-ico">📊</span>Dashboard</a>
        {% endif %}
        <a class="{{'on' if page=='consumos'}}" href="{{url_for('consumos')}}"><span class="nav-ico">🍽️</span>Consumos</a>
        {% if session.get('role') == 'admin' %}
        <a class="{{'on' if page=='trabajadores'}}" href="{{url_for('trabajadores')}}"><span class="nav-ico">👥</span>Trabajadores</a>
        {% endif %}
        <a class="{{'on' if page=='entregas'}}" href="{{url_for('entregas')}}"><span class="nav-ico">🚚</span>Entregas <span class="pill nuevo">NUEVO</span></a>
        {% if session.get('role') == 'admin' %}
        <a class="{{'on' if page=='reportes'}}" href="{{url_for('reportes')}}"><span class="nav-ico">📁</span>Reportes <span class="pill correo">CORREO</span></a>
        {% endif %}
        <a class="{{'on' if page=='cierre'}}" href="{{url_for('cierre_dia')}}"><span class="nav-ico">📁</span>Cerrar día</a>
        {% if session.get('role') == 'admin' %}
        <a class="{{'on' if page=='carga'}}" href="{{url_for('carga_masiva')}}"><span class="nav-ico">📥</span>Carga Masiva</a>
        <a class="{{'on' if page=='config'}}" href="{{url_for('configuracion')}}"><span class="nav-ico">⚙️</span>Config. / Usuarios</a>
        {% endif %}
        <a class="logout-link" href="{{url_for('logout')}}"><span class="nav-ico">🚪</span>Salir</a>
      </nav>

      <div class="side-slogan-card">
        <div class="leaf-icon"></div>
        <b>Comer bien,</b><br>es vivir mejor.
      </div>
    </aside>

    <main class="content">
      {% with messages=get_flashed_messages(with_categories=true) %}
        {% for c,m in messages %}
          <div class="flash {{c}}">{{m}}</div>
        {% endfor %}
      {% endwith %}
      {{content|safe}}
    </main>

    <aside class="panel-right">
      <div class="card status-box">
        <h3 style="margin-top:0">Estado del día</h3>
        <div class="status-inner">
          <span class="badge {{'off' if cerrado_hoy else 'ok'}}">🟢 {{'DÍA CERRADO' if cerrado_hoy else 'DÍA ABIERTO'}}</span>
          <p class="small" style="line-height:1.7">
            <b>Fecha:</b> {{fecha_hoy}}<br>
            <b>{{'Cerrado' if cerrado_hoy else 'Abierto'}} por:</b> admin (08:00 AM)
          </p>
          {% if not cerrado_hoy %}
            <a class="btn btn-orange" style="width:100%;text-align:center" href="{{url_for('cierre_dia')}}">Cerrar día y consolidar</a>
          {% endif %}
        </div>
      </div>

      <div class="card">
        <h3 style="margin-top:0">Acciones rápidas</h3>
        <div class="quick">
          <a href="{{url_for('consumos')}}">🔹 Registrar consumo</a>
          <a href="{{url_for('entregas')}}">🚚 Entrega de pedidos</a>
          <a href="{{url_for('carga_masiva')}}">📥 Carga masiva de consumos</a>
          <a href="{{url_for('reportes')}}">✉️ Enviar reporte por correo</a>
        </div>
      </div>
    </aside>
  </div>

  <footer class="footer">
    <span>© 2026 Prize Superfruits - Comedor Corporativo. Todos los derechos reservados.</span>
    <span>Versión 2.0.0</span>
  </footer>
</div>
{% endif %}
<script>
// ===== PRO TOTAL: DNI automático + cámara QR/BARRAS para CONSUMOS =====
(function(){
  let proTimer = null;
  let proScanner = null;
  let proStream = null;
  let proBusy = false;

  function dniClean(v){
    const raw = String(v || '').trim();
    const only = raw.replace(/\D/g,'');
    if (only.length === 8) return only;
    const labeled = raw.toUpperCase().match(/(?:DNI|DOC(?:UMENTO)?|DOCUMENT|NRO|NUMERO|NÚMERO)\D{0,20}(\d{8})(?!\d)/);
    if (labeled) return labeled[1];
    const eight = raw.match(/(^|\D)(\d{8})(?!\d)/);
    if (eight) return eight[2];
    if (only.length > 8) return only.slice(-8);
    return only.slice(0,8);
  }
  function toast(msg, ok=true){
    let d = document.createElement('div');
    d.textContent = msg;
    d.style.cssText = 'position:fixed;left:12px;right:12px;bottom:18px;z-index:999999;padding:13px 15px;border-radius:13px;font-weight:900;color:white;text-align:center;box-shadow:0 10px 28px rgba(0,0,0,.25);background:'+(ok?'#17a34a':'#b91c1c');
    document.body.appendChild(d); setTimeout(()=>d.remove(), 1800);
    try{ if(navigator.vibrate) navigator.vibrate(ok?90:[80,50,80]); }catch(e){}
  }
  function beep(){
    try{
      const C = window.AudioContext || window.webkitAudioContext;
      const c = new C(); const o = c.createOscillator(); const g = c.createGain();
      o.connect(g); g.connect(c.destination); o.frequency.value = 920; g.gain.value = .08;
      o.start(); setTimeout(()=>{o.stop(); c.close();}, 130);
    }catch(e){}
  }
  function setNombre(data, dni){
    const nombre = document.getElementById('nombre_trabajador') || document.querySelector('[name="nombre"],#nombre');
    const info = document.getElementById('info_trabajador_consumo');
    if(data && (data.ok || data.success || data.nombre)){
      if(nombre){ nombre.value = data.nombre || ''; nombre.title = data.nombre || ''; }
      if(info){
        info.style.display='block';
        info.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px"><div><b>Trabajador</b><br>'+(data.nombre||'-')+'</div><div><b>DNI</b><br>'+dni+'</div><div><b>Área</b><br>'+(data.area||'-')+'</div><div><b>Estado</b><br><span class="badge ok">Activo</span></div></div>';
      }
      beep(); return true;
    } else {
      if(nombre){ nombre.value = 'DNI no encontrado'; nombre.title = 'DNI no encontrado'; }
      if(info){ info.style.display='block'; info.innerHTML='<span style="color:#991b1b">DNI no encontrado en Trabajadores: '+dni+'</span>'; }
      return false;
    }
  }
  async function buscarAutoDniConsumo(force=false){
    const inp = document.getElementById('dni_consumo') || document.querySelector('input[name="dni"]');
    if(!inp) return;
    const dni = dniClean(inp.value);
    inp.value = dni;
    const nombre = document.getElementById('nombre_trabajador') || document.querySelector('[name="nombre"],#nombre');
    if(dni.length < 8){ if(nombre) nombre.value=''; return; }
    if(nombre) nombre.value = 'Validando DNI...';
    try{
      const r = await fetch('/api/trabajador/' + encodeURIComponent(dni) + '?_=' + Date.now(), {cache:'no-store', credentials:'same-origin'});
      const data = await r.json();
      const ok = setNombre(data, dni);
      if(ok && document.getElementById('modo_lote')?.checked && typeof agregarDniLote === 'function'){
        setTimeout(()=>agregarDniLote(dni, data.nombre || ''), 80);
      }
    }catch(e){ if(nombre) nombre.value='Error validando DNI'; toast('No se pudo consultar el DNI.', false); }
  }
  window.buscarAutoDniConsumo = buscarAutoDniConsumo;
  window.dniInputHandler = function(){
    const inp = document.getElementById('dni_consumo') || document.querySelector('input[name="dni"]');
    if(!inp) return;
    inp.value = dniClean(inp.value);
    clearTimeout(proTimer);
    proTimer = setTimeout(()=>buscarAutoDniConsumo(false), inp.value.length === 8 ? 20 : 120);
  };
  async function procesarLectura(texto){
    if(proBusy) return;
    const dni = dniClean(texto);
    if(dni.length !== 8){ toast('No detecté un DNI de 8 dígitos.', false); return; }
    proBusy = true;
    const inp = document.getElementById('dni_consumo') || document.querySelector('input[name="dni"]');
    if(inp) inp.value = dni;
    await buscarAutoDniConsumo(true);
    toast('DNI leído: ' + dni, true);
    setTimeout(()=>{proBusy=false;}, 900);
  }
  window.abrirScannerQR = async function(){
    let cont = document.getElementById('qr-reader');
    if(!cont){
      cont = document.createElement('div'); cont.id = 'qr-reader';
      const form = document.getElementById('form_consumo') || document.body;
      form.appendChild(cont);
    }
    if(location.protocol !== 'https:' && !['localhost','127.0.0.1'].includes(location.hostname)){
      toast('La cámara requiere HTTPS. Usa el enlace de Render con https://', false);
    }
    cont.style.display='block';
    cont.innerHTML = '<div style="grid-column:1/-1;padding:12px;border:1px solid #dce6f0;border-radius:14px;background:#f8fbff"><b>📷 Cámara QR / Barras activa</b><div id="qr-reader-live" style="width:100%;max-width:460px;margin-top:8px"></div><video id="qr-video-live" playsinline muted autoplay style="display:none;width:100%;max-width:460px;border-radius:12px;margin-top:8px;background:#000"></video><canvas id="qr-canvas-live" style="display:none"></canvas><button type="button" class="btn-red" style="margin-top:8px" onclick="cerrarScannerQR()">Cerrar cámara</button><br><small>Permite la cámara. En celular usa Chrome y HTTPS.</small></div>';
    try{
      if(window.Html5Qrcode){
        const formats = window.Html5QrcodeSupportedFormats ? [
          Html5QrcodeSupportedFormats.QR_CODE, Html5QrcodeSupportedFormats.CODE_128,
          Html5QrcodeSupportedFormats.CODE_39, Html5QrcodeSupportedFormats.EAN_13,
          Html5QrcodeSupportedFormats.EAN_8, Html5QrcodeSupportedFormats.ITF,
          Html5QrcodeSupportedFormats.UPC_A, Html5QrcodeSupportedFormats.UPC_E,
          Html5QrcodeSupportedFormats.PDF_417
        ].filter(Boolean) : undefined;
        proScanner = new Html5Qrcode('qr-reader-live', formats ? {formatsToSupport:formats, verbose:false} : undefined);
        await proScanner.start({facingMode:{ideal:'environment'}}, {fps:15, qrbox:{width:280,height:190}, rememberLastUsedCamera:true}, async txt=>{
          await procesarLectura(txt);
          if(!document.getElementById('modo_lote')?.checked) cerrarScannerQR();
        }, ()=>{});
        toast('Cámara activada.', true); return;
      }
    }catch(e){ console.warn('html5-qrcode no abrió, usando respaldo', e); }
    await scannerNativo();
  };
  async function scannerNativo(){
    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('Navegador sin cámara');
    const video = document.getElementById('qr-video-live'); const canvas = document.getElementById('qr-canvas-live');
    const live = document.getElementById('qr-reader-live'); if(live) live.innerHTML='<b>Respaldo con cámara nativa...</b>';
    proStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}}, audio:false});
    video.srcObject = proStream; video.style.display='block'; await video.play();
    let detector = null;
    if('BarcodeDetector' in window){ try{ detector = new BarcodeDetector({formats:['qr_code','code_128','code_39','ean_13','ean_8','itf','upc_a','upc_e','pdf417']}); }catch(e){} }
    const loop = async()=>{
      if(!proStream) return;
      try{
        if(detector){ const codes = await detector.detect(video); if(codes && codes.length){ await procesarLectura(codes[0].rawValue||''); if(!document.getElementById('modo_lote')?.checked){cerrarScannerQR(); return;} } }
        if(window.jsQR && video.videoWidth){
          canvas.width=video.videoWidth; canvas.height=video.videoHeight;
          const ctx=canvas.getContext('2d',{willReadFrequently:true}); ctx.drawImage(video,0,0,canvas.width,canvas.height);
          const img=ctx.getImageData(0,0,canvas.width,canvas.height); const code=jsQR(img.data,img.width,img.height);
          if(code && code.data){ await procesarLectura(code.data); if(!document.getElementById('modo_lote')?.checked){cerrarScannerQR(); return;} }
        }
      }catch(e){}
      requestAnimationFrame(loop);
    };
    toast('Cámara activada.', true); requestAnimationFrame(loop);
  }
  window.cerrarScannerQR = function(){
    try{ if(proScanner && proScanner.stop) proScanner.stop().catch(()=>{}).finally(()=>{try{proScanner.clear();}catch(e){}}); }catch(e){}
    try{ if(proStream){ proStream.getTracks().forEach(t=>t.stop()); } }catch(e){}
    proScanner=null; proStream=null;
    const cont=document.getElementById('qr-reader'); if(cont){cont.style.display='none'; cont.innerHTML='';}
  };
  document.addEventListener('DOMContentLoaded', function(){
    const inp = document.getElementById('dni_consumo');
    if(inp){
      inp.setAttribute('oninput','dniInputHandler()');
      inp.setAttribute('onkeyup','dniInputHandler()');
      inp.addEventListener('input', window.dniInputHandler, true);
      inp.addEventListener('paste', ()=>setTimeout(window.dniInputHandler, 30), true);
      inp.addEventListener('keydown', e=>{ if(e.key==='Enter'){e.preventDefault(); buscarAutoDniConsumo(true);} }, true);
      setTimeout(()=>inp.focus(),250);
    }
    const btn = document.getElementById('btn_qr');
    if(btn) btn.onclick = window.abrirScannerQR;
  });
})();
</script>

</body>
</html>
"""


def render_page(content, page=""):
    pendientes_count = q_one(
        "SELECT COUNT(*) c FROM consumos WHERE fecha=? AND estado='PENDIENTE'",
        (hoy_iso(),)
    )["c"]
    return render_template_string(
        BASE_HTML,
        content=content,
        page=page,
        pendientes_count=pendientes_count,
        cerrado_hoy=bool(dia_cerrado()),
        fecha_hoy=fecha_peru_txt(),
        money=money,
    )


def topbar(title, subtitle="Resumen general del sistema"):
    return f"""
    <div class="topbar">
      <div>
        <h2>{title}</h2>
        <div class="muted">{subtitle}</div>
      </div>
      <div class="user-chip">
        <span style="font-size:24px">🔔</span>
        <div class="avatar">👤</div>
        <div><span class="small">Bienvenido,</span><br>{session.get('user','')}</div>
      </div>
    </div>
    """


# =========================
# RUTAS
# =========================

@app.route("/cerrar_dia_manual")
@login_required
@roles_required("admin")
def cerrar_dia_manual():
    fecha = hoy_iso()
    if dia_cerrado(fecha):
        flash("El día ya estaba cerrado.", "error")
    else:
        q_exec("""
            INSERT INTO cierres(fecha,cerrado_por,total_consumos,total_entregados,total_pendientes,total_importe,archivo_excel,correo_destino,correo_estado)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (fecha, session["user"], 0, 0, 0, 0, "", "", "CIERRE MANUAL"))
        flash("Día cerrado manualmente por administrador.", "ok")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/abrir_dia_manual")
@login_required
@roles_required("admin")
def abrir_dia_manual():
    fecha = hoy_iso()
    q_exec("DELETE FROM cierres WHERE fecha=?", (fecha,))
    flash("Día abierto/reabierto correctamente por administrador.", "ok")
    return redirect(request.referrer or url_for("dashboard"))


def rows_filtrados_desde_request(solo_entregados=False):
    fecha_inicio = request.args.get("fecha_inicio") or request.args.get("fecha") or hoy_iso()
    fecha_fin = request.args.get("fecha_fin") or fecha_inicio
    buscar = clean_text(request.args.get("buscar"))
    cond, params = rango_sql(fecha_inicio, fecha_fin)
    where = cond
    final_params = list(params)
    if solo_entregados:
        where += " AND estado='ENTREGADO'"
    if buscar:
        where += " AND (dni LIKE ? OR trabajador LIKE ? OR area LIKE ? OR fundo LIKE ? OR comedor LIKE ?)"
        b = f"%{buscar}%"
        final_params += [b, b, b, b, b]
    rows = q_all(f"SELECT * FROM consumos WHERE {where} ORDER BY fecha,hora,id", tuple(final_params))
    return fecha_inicio, fecha_fin, buscar, rows


@app.route("/exportar_concesionaria")
@login_required
def exportar_concesionaria():
    fecha_inicio, fecha_fin, buscar, rows = rows_filtrados_desde_request(False)
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        df = pd.DataFrame(columns=["fecha","hora","dni","trabajador","area","tipo","comedor","fundo","responsable","cantidad","total","estado"])
    filename = f"consumos_concesionaria_{fecha_inicio.replace('-','_')}_a_{fecha_fin.replace('-','_')}.xlsx"
    path = os.path.join(CONCESIONARIA_DIR, filename)
    df.to_excel(path, index=False)
    return send_file(path, as_attachment=True)


@app.route("/reporte_entrega")
@login_required
def reporte_entrega():
    fecha_inicio, fecha_fin, buscar, rows = rows_filtrados_desde_request(True)
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        df = pd.DataFrame(columns=["fecha","hora","dni","trabajador","area","tipo","comedor","fundo","responsable","cantidad","total","estado"])
    filename = f"reporte_entrega_pago_{fecha_inicio.replace('-','_')}_a_{fecha_fin.replace('-','_')}.xlsx"
    path = os.path.join(ENTREGAS_DIR, filename)
    df.to_excel(path, index=False)
    return send_file(path, as_attachment=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        password = request.form.get("password", "")
        user = q_one("SELECT * FROM usuarios WHERE username=? AND active=1", (username,))
        if user and check_password_hash(user["password_hash"], password):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        flash("Usuario o clave incorrecta.", "error")

    html = """
    <div class="login-page">
      <div class="login-card">
        <div class="login-inner">
          <div class="logo-word">Priz<span class="e">e<span class="leaf"></span></span></div>
          <h2 class="login-title">Sistema Comedor PRIZE</h2>
          <p class="login-subtitle">Acceso al sistema</p>

          <form method="post">
            <div class="form-label">Usuario</div>
            <div class="input-icon"><span>👤</span><input name="username" placeholder="Ingrese su usuario" required></div>

            <div class="form-label">Clave</div>
            <div class="input-icon"><span>🔒</span><input name="password" type="password" placeholder="Ingrese su clave" required></div>

            <button class="login-button">Ingresar</button>
          </form>

        </div>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, content=html)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("consumos"))
    fecha_inicio = request.args.get("fecha_inicio") or request.args.get("fecha") or hoy_iso()
    fecha_fin = request.args.get("fecha_fin") or fecha_inicio
    buscar = clean_text(request.args.get("buscar"))
    cond, params = rango_sql(fecha_inicio, fecha_fin)

    where = cond
    final_params = list(params)
    if buscar:
        where += " AND (dni LIKE ? OR trabajador LIKE ? OR area LIKE ? OR fundo LIKE ? OR comedor LIKE ?)"
        b = f"%{buscar}%"
        final_params += [b, b, b, b, b]

    total_filtro = q_one(f"SELECT COUNT(*) c, COALESCE(SUM(total),0) t FROM consumos WHERE {where}", tuple(final_params))
    entregados = q_one(f"SELECT COUNT(*) c FROM consumos WHERE {where} AND estado='ENTREGADO'", tuple(final_params))["c"]
    pendientes = q_one(f"SELECT COUNT(*) c FROM consumos WHERE {where} AND estado='PENDIENTE'", tuple(final_params))["c"]
    trabajadores = q_one("SELECT COUNT(*) c FROM trabajadores WHERE activo=1")["c"]

    rows = q_all(f"SELECT * FROM consumos WHERE {where} ORDER BY fecha DESC,hora DESC,id DESC LIMIT 12", tuple(final_params))
    tabla = "".join([
        f"""
        <tr>
          <td>{i}</td><td>{r['fecha']}</td><td>{r['hora']}</td><td>{r['dni']}</td><td>{r['trabajador']}</td>
          <td>{r['area']}</td><td>{r['tipo']}</td><td>{r['comedor']}</td><td>{r['fundo']}</td>
          <td>{r['cantidad']}</td><td>{money(r['total'])}</td>
        </tr>
        """
        for i, r in enumerate(rows, 1)
    ]) or "<tr><td colspan='11'>Sin consumos con el filtro seleccionado.</td></tr>"

    admin_buttons = ""
    if session.get("role") == "admin":
        admin_buttons = f"""
        <div class="admin-actions">
          <a class="btn btn-orange" href="{url_for('cerrar_dia_manual')}">🔒 Cerrar día</a>
          <a class="btn btn-blue" href="{url_for('abrir_dia_manual')}">🔓 Abrir día</a>
        </div>
        """

    html = topbar("Dashboard", "Indicadores filtrados por día, mes o año") + admin_buttons
    html += filtro_bar(url_for("dashboard"), fecha_inicio, fecha_fin, buscar)
    html += f"""
    <div class="kpi-grid">
      <div class="card kpi-card"><div class="icon-circle ic-green">🍴</div><div><div class="label">Consumos filtrados</div><div class="num">{total_filtro['c']}</div><div class="sub">RANGO</div></div></div>
      <div class="card kpi-card"><div class="icon-circle ic-blue">✅</div><div><div class="label">Entregados</div><div class="num">{entregados}</div><div class="sub">confirmados</div></div></div>
      <div class="card kpi-card"><div class="icon-circle ic-purple">⏳</div><div><div class="label">Pendientes</div><div class="num">{pendientes}</div><div class="sub">por entregar</div></div></div>
      <div class="card kpi-card"><div class="icon-circle ic-orange">S/</div><div><div class="label">Total filtrado</div><div class="num" style="color:#16a34a">{money(total_filtro['t'])}</div><div class="sub">trabajadores activos: {trabajadores}</div></div></div>
    </div>

    <div class="card">
      <div class="table-head">
        <h3>Consumos filtrados</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <a class="btn btn-blue" href="{url_for('exportar_concesionaria', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, buscar=buscar)}">Archivo concesionaria</a>
          <a class="btn btn-orange" href="{url_for('reporte_entrega', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, buscar=buscar)}">Reporte entrega/pago</a>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>#</th><th>Fecha</th><th>Hora</th><th>DNI</th><th>Trabajador</th><th>Área</th><th>Tipo</th><th>Comedor</th><th>Fundo</th><th>Cant.</th><th>Total</th></tr>
          {tabla}
        </table>
      </div>
    </div>
    """
    return render_page(html, "dashboard")



@app.route("/api/trabajador/<dni>")
@login_required
def api_trabajador(dni):
    dni = clean_dni(dni)
    t = q_one("SELECT dni,nombre,empresa,area,cargo FROM trabajadores WHERE dni=? AND activo=1", (dni,))
    if not t:
        resp = jsonify({"ok": False, "success": False, "msg": "DNI no encontrado"})
    else:
        resp = jsonify({"ok": True, "success": True, "dni": t["dni"], "nombre": t["nombre"], "empresa": t["empresa"], "area": t["area"], "cargo": t["cargo"]})
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp

@app.route("/api/buscar_dni/<dni>")
@login_required
def api_buscar_dni(dni):
    return api_trabajador(dni)

@app.route("/buscar_trabajador/<dni>")
@login_required
def buscar_trabajador_compat(dni):
    return api_trabajador(dni)

@app.route("/api/trabajador")
@login_required
def api_trabajador_query():
    return api_trabajador(request.args.get("dni", ""))

@app.route("/consumos", methods=["GET", "POST"])
@login_required
@roles_required("admin", "rrhh", "comedor")
def consumos():
    if request.method == "POST":
        fecha = request.form.get("fecha") or hoy_iso()

        if fecha != hoy_iso():
            flash("Solo se puede registrar consumo en la fecha actual de hoy. Las fechas anteriores o futuras son solo de consulta.", "error")
            return redirect(url_for("consumos", fecha=fecha))

        if dia_cerrado(fecha):
            flash("El día ya está cerrado. No se puede registrar consumos. Al día siguiente el sistema abrirá automáticamente la nueva fecha.", "error")
            return redirect(url_for("consumos", fecha=fecha))

        bloqueado, msg = registro_bloqueado()
        if bloqueado and session.get("role") != "admin":
            flash(msg, "error")
            return redirect(url_for("consumos"))

        tipo = request.form.get("tipo", "Almuerzo")
        if tipo not in ["Almuerzo", "Dieta"]:
            tipo = "Almuerzo"

        comedor = request.form.get("comedor", "Comedor 01")
        fundo = request.form.get("fundo", "Kawsay Allpa")
        responsable = clean_text(request.form.get("responsable")).upper()
        if not responsable:
            flash("El campo RESPONSABLE es obligatorio y debe ir en MAYÚSCULAS.", "error")
            return redirect(url_for("consumos", fecha=fecha))
        cantidad = int(float(request.form.get("cantidad") or 1))
        precio = float(request.form.get("precio_unitario") or 10)
        total = cantidad * precio
        obs = clean_text(request.form.get("observacion"))
        es_adicional = 1 if request.form.get("adicional") == "1" and session.get("role") == "admin" else 0

        # REGISTRO MASIVO / EN LOTE desde la misma pestaña Consumos.
        if request.form.get("modo_lote") == "1":
            lote_raw = request.form.get("dni_lote", "")
            dnis = []
            for part in re.split(r"[\s,;]+", lote_raw):
                d = clean_dni(part)
                if d and d not in dnis:
                    dnis.append(d)
            # PRO FIX: si el navegador no alcanzó a llenar el textarea oculto,
            # usamos el DNI visible como respaldo en el clic final de REGISTRO DE CONSUMO.
            if not dnis:
                d_respaldo = clean_dni(request.form.get("dni"))
                if d_respaldo and len(d_respaldo) == 8:
                    dnis.append(d_respaldo)
            if not dnis:
                flash("Registro masivo activo, pero aún no hay DNIs guardados en el lote. Digita o escanea un DNI válido y espera el mensaje verde de guardado.", "error")
                return redirect(url_for("consumos", fecha=fecha))
            creados, errores = 0, []
            for dni in dnis:
                trabajador = q_one("SELECT * FROM trabajadores WHERE dni=? AND activo=1", (dni,))
                if not trabajador:
                    errores.append(f"{dni}: DNI no encontrado o errado")
                    continue
                if not es_adicional and q_one("SELECT id FROM consumos WHERE fecha=? AND dni=? AND COALESCE(adicional,0)=0", (fecha, dni)):
                    errores.append(f"{dni}: ya tiene consumo registrado hoy")
                    continue
                try:
                    q_exec("""
                        INSERT INTO consumos(fecha,hora,dni,trabajador,empresa,area,tipo,cantidad,precio_unitario,total,observacion,comedor,fundo,responsable,adicional,estado,creado_por)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (fecha, hora_now(), dni, trabajador["nombre"], trabajador["empresa"], trabajador["area"], tipo, cantidad, precio, total, obs, comedor, fundo, responsable, es_adicional, "PENDIENTE", session["user"]))
                    creados += 1
                except Exception as e:
                    errores.append(f"{dni}: no se pudo registrar")
            msg = f"REGISTRO DE CONSUMO terminado: {creados} consumo(s) registrado(s) para la fecha {fecha_peru_txt(fecha)}."
            if errores:
                msg += " Alertas: " + " | ".join(errores[:12])
                if len(errores) > 12:
                    msg += f" | y {len(errores)-12} más."
            flash(msg, "ok" if not errores else "error")
            return redirect(url_for("consumos", fecha=fecha))

        dni = clean_dni(request.form.get("dni"))
        trabajador = q_one("SELECT * FROM trabajadores WHERE dni=? AND activo=1", (dni,))
        if not trabajador:
            flash("DNI no encontrado o trabajador inactivo.", "error")
            return redirect(url_for("consumos"))

        # REGLA FUERTE: 1 DNI = 1 consumo normal por día.
        if not es_adicional:
            duplicado = q_one("SELECT id,hora,tipo FROM consumos WHERE fecha=? AND dni=? AND COALESCE(adicional,0)=0", (fecha, dni))
            if duplicado:
                flash(f"NO DUPLICADO: el DNI {dni} ya tiene consumo registrado hoy a las {duplicado['hora']}. Solo el admin puede registrar adicional.", "error")
                return redirect(url_for("consumos"))

        try:
            q_exec("""
                INSERT INTO consumos(fecha,hora,dni,trabajador,empresa,area,tipo,cantidad,precio_unitario,total,observacion,comedor,fundo,responsable,adicional,estado,creado_por)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (fecha, hora_now(), dni, trabajador["nombre"], trabajador["empresa"], trabajador["area"], tipo, cantidad, precio, total, obs, comedor, fundo, responsable, es_adicional, "PENDIENTE", session["user"]))
        except Exception:
            flash(f"NO DUPLICADO: el DNI {dni} ya tiene consumo registrado para el día {fecha_peru_txt(fecha)}.", "error")
            return redirect(url_for("consumos"))

        flash("REGISTRO DE CONSUMO realizado correctamente." + (" Marcado como adicional." if es_adicional else ""), "ok")
        return redirect(url_for("consumos"))

    fecha = request.args.get("fecha") or hoy_iso()
    fecha_inicio = request.args.get("fecha_inicio") or fecha
    fecha_fin = request.args.get("fecha_fin") or fecha_inicio
    buscar = clean_text(request.args.get("buscar"))
    cond, params = rango_sql(fecha_inicio, fecha_fin)
    where = cond
    final_params = list(params)
    if buscar:
        where += " AND (dni LIKE ? OR trabajador LIKE ? OR area LIKE ? OR fundo LIKE ? OR comedor LIKE ? OR responsable LIKE ? OR tipo LIKE ?)"
        b = f"%{buscar}%"
        final_params += [b, b, b, b, b, b, b]

    rows = q_all(f"SELECT * FROM consumos WHERE {where} ORDER BY fecha DESC,hora DESC,id DESC", tuple(final_params))
    tabla = "".join([
        f"""
        <tr>
          <td>{r['fecha']}</td>
          <td>{r['hora']}</td>
          <td>{r['dni']}</td>
          <td>{r['trabajador']}</td>
          <td>{r['area']}</td>
          <td>{r['tipo']}{' + Adic.' if r['adicional'] else ''}</td>
          <td>{r['comedor']}</td>
          <td>{r['fundo']}</td>
          <td>{r['responsable'] or '-'}</td>
          <td>{r['cantidad']}</td>
          <td>{money(r['precio_unitario'])}</td>
          <td>{money(r['total'])}</td>
          <td><span class="badge {'ok' if r['estado']=='ENTREGADO' else 'warn'}">{r['estado']}</span></td>
          <td>
            <form method="post" action="{url_for('quitar_consumo')}" style="display:flex;gap:6px;align-items:center">
              <input type="hidden" name="id" value="{r['id']}">
              <input name="clave" placeholder="Clave" style="width:85px;padding:8px">
              <button class="btn-red" style="padding:8px 10px">Quitar</button>
            </form>
          </td>
        </tr>
        """ for r in rows
    ]) or "<tr><td colspan='14'>Sin registros para este filtro.</td></tr>"

    fecha_cerrada = bool(dia_cerrado(fecha))
    fecha_es_hoy = (fecha == hoy_iso())
    disabled = "disabled" if (fecha_cerrada or not fecha_es_hoy) else ""
    bloqueado, msg_bloq = registro_bloqueado()
    aviso_bloq = f"<div class='flash error'>{msg_bloq}</div>" if bloqueado and session.get("role") != "admin" else ""
    aviso_fecha = ""
    if fecha_cerrada:
        aviso_fecha = "<div class='flash error'>Esta fecha está CERRADA. Puedes revisarla, pero no registrar nuevos consumos.</div>"
    elif not fecha_es_hoy:
        aviso_fecha = "<div class='flash error'>Fecha seleccionada solo para consulta. El registro de consumo solo está permitido en la fecha actual de hoy.</div>"

    filtros = filtro_bar(url_for("consumos"), fecha_inicio, fecha_fin, buscar)

    html = topbar("Registro y control de consumos", "Registra por digitación o lector QR usando el DNI") + f"""
    {aviso_bloq}
    {aviso_fecha}

    <div class="card">
      <h3 style="margin-top:0">Registrar consumo</h3>
      <form method="post" class="form-grid" id="form_consumo" onsubmit="return validarAntesEnviar(event)">
        <input type="date" name="fecha" value="{fecha}" onchange="window.location='{url_for('consumos')}?fecha=' + this.value" title="Elige una fecha para consultar. Solo hoy permite registrar." max="{hoy_iso()}">
        <input id="dni_consumo" name="dni" placeholder="Digite DNI o escanee QR/barras" required autofocus inputmode="numeric" pattern="[0-9]*" maxlength="8" autocomplete="off" enterkeyhint="next" oninput="dniInputHandler()" onkeyup="dniInputHandler()" onchange="dniInputHandler()" {disabled}>
        <input id="nombre_trabajador" class="worker-name-field" placeholder="Nombre aparecerá automáticamente al digitar DNI" readonly title="Nombre completo del trabajador" {disabled}>
        <button type="button" class="btn-blue" onclick="buscarTrabajadorConsumo(true)" {disabled}>🔎 Buscar trabajador</button>
        <button type="button" id="btn_qr" class="btn-blue" onclick="abrirScannerQR()" {disabled}>📷 Cámara QR / Barras</button>
        <div id="info_trabajador_consumo" style="display:none;grid-column:1/-1;border:1px solid #bbf7d0;background:#f0fdf4;border-radius:14px;padding:12px;font-weight:900;color:#14532d"></div>
        <div id="qr-reader" style="display:none;width:420px;max-width:100%;margin:10px 0;grid-column:1/-1"></div>
        <select name="comedor" {disabled}>
          {''.join([f'<option>{c}</option>' for c in opciones_comedor()])}
        </select>
        <select name="tipo" {disabled}>
          <option>Almuerzo</option>
          <option>Dieta</option>
        </select>
        <select name="fundo" {disabled}>
          {''.join([f'<option>{f}</option>' for f in opciones_fundo()])}
        </select>
        <input name="responsable" placeholder="RESPONSABLE (OBLIGATORIO MAYÚSCULAS)" required style="text-transform:uppercase" oninput="this.value=this.value.toUpperCase()" {disabled}>
        <input type="number" name="cantidad" min="1" value="1" {disabled}>
        <input type="number" step="0.01" name="precio_unitario" value="10.00" {disabled}>
        <input name="observacion" placeholder="Observación / QR DNI" {disabled}>
        <label style="font-weight:900"><input type="checkbox" id="modo_lote" name="modo_lote" value="1" onchange="toggleLote()"> Registro masivo / lote</label>
        {('<label style="font-weight:900"><input type="checkbox" name="adicional" value="1"> Consumo adicional</label>' if session.get('role')=='admin' else '')}
        <div id="lote_panel" class="lote-dios-panel">
          <div class="lote-dios-head">
            <div>
              <div class="lote-dios-title">📦 REGISTRO MASIVO / LOTE EN VIVO</div>
              <div class="lote-dios-sub">La cámara queda encendida. Cada DNI detectado aparece aquí antes del clic final.</div>
            </div>
            <div class="lote-dios-counter"><b id="lote_total_big">0</b><span>TRABAJADORES EN LOTE</span></div>
          </div>
          <div class="lote-dios-status">
            <div>📷 Cámara: <span id="camara_estado_lote">apagada</span></div>
            <div>✅ Validados: <span id="lote_count">0</span></div>
            <div>🕒 Último DNI: <span id="ultimo_dni_lote">-</span></div>
          </div>
          <div class="lote-dios-list-head">
            <div>#</div><div>DNI</div><div>Trabajador detectado</div><div>Estado</div><div>Quitar</div>
          </div>
          <div id="lote_lista" class="lote-dios-list"></div>
          <div class="lote-dios-actions">
            <button type="button" class="btn-blue" style="min-height:36px;padding:8px 12px" onclick="agregarActualAlLote()">➕ Agregar DNI digitado</button>
            <button type="button" class="btn-red" style="min-height:36px;padding:8px 12px" onclick="limpiarLoteConsumos()">Limpiar lote</button>
          </div>
        </div>
        <textarea id="dni_lote" name="dni_lote" placeholder="DNIs validados para lote" style="display:none;grid-column:1/-1;min-height:90px"></textarea>
        <textarea id="lote_detalle" name="lote_detalle" style="display:none"></textarea>
        <button id="btn_submit_consumo" {disabled}>REGISTRO DE CONSUMO</button>
        <a class="btn btn-blue" href="{url_for('consumos')}">Actualizar / refrescar</a>
      </form>
      <p class="muted small">Regla: no se permite duplicar DNI para el mismo día. Al digitar el DNI aparecerá automáticamente el nombre del trabajador.</p>
    </div>
    <script>
    let dniTimer = null;
    let ultimoDniValidado = '';
    let qrActivo = null;
    let scannerBusy = false;
    let ultimoScanDni = '';
    let ultimoScanTs = 0;

    function soloDni(v){{
      const raw = String(v || '').trim();
      if(!raw) return '';
      const only = raw.replace(/\D/g,'');
      if(only.length === 8) return only;
      const labeled = raw.toUpperCase().match(/(?:DNI|DOC(?:UMENTO)?|NRO|NÚMERO|NUMERO|DOCUMENT)\D{{0,16}}(\d{{8}})(?!\d)/);
      if(labeled) return labeled[1];
      const standalone = raw.match(/(^|\D)(\d{{8}})(?!\d)/);
      if(standalone) return standalone[2];
      if(only.length > 8) return only.slice(-8);
      return only.slice(0,8);
    }}
    function getLoteArray(){{
      const box = document.getElementById('dni_lote');
      if(!box) return [];
      return (box.value || '').split(/[\s,;|]+/).map(soloDni).filter(x => x.length === 8);
    }}
    function getLoteDetalle(){{
      const det = document.getElementById('lote_detalle');
      if(!det || !det.value) return {{}};
      try{{return JSON.parse(det.value || '{{}}') || {{}};}}catch(e){{return {{}};}}
    }}
    function setLoteDetalle(obj){{
      const det = document.getElementById('lote_detalle');
      if(det) det.value = JSON.stringify(obj || {{}});
      try{{ localStorage.setItem('lote_consumos_detalle_' + new Date().toISOString().slice(0,10), JSON.stringify(obj || {{}})); }}catch(e){{}}
    }}
    function setLoteArray(arr, detalle=null){{
      const limpio = [];
      arr.forEach(d => {{ d = soloDni(d); if(d && d.length === 8 && !limpio.includes(d)) limpio.push(d); }});
      const oldDetalle = detalle || getLoteDetalle();
      const nuevoDetalle = {{}};
      limpio.forEach(d => {{ nuevoDetalle[d] = oldDetalle[d] || ''; }});
      const box = document.getElementById('dni_lote');
      const lista = document.getElementById('lote_lista');
      const count = document.getElementById('lote_count');
      if(box) box.value = limpio.join('\n');
      setLoteDetalle(nuevoDetalle);
      if(count) count.textContent = limpio.length + ' DNI';
      const big = document.getElementById('lote_total_big');
      if(big) big.textContent = limpio.length;
      const ultimo = document.getElementById('ultimo_dni_lote');
      if(ultimo) ultimo.textContent = limpio.length ? limpio[limpio.length-1] : '-';
      if(lista){{
        lista.innerHTML = limpio.length
          ? limpio.map((d, i) => `<div class="lote-dios-row"><b>${{i+1}}</b><b>${{d}}</b><span>${{(nuevoDetalle[d] || 'Trabajador validado')}}</span><span class="ok">VALIDADO</span><button type="button" onclick="quitarDniLote('${{d}}')" style="min-height:0;width:38px;padding:6px;border-radius:999px;background:#ef4444;box-shadow:none">×</button></div>`).join('')
          : '<div class="lote-dios-empty">Aún no hay DNIs guardados. Digita o escanea para acumular antes del clic final.</div>';
      }}
      try{{ localStorage.setItem('lote_consumos_' + new Date().toISOString().slice(0,10), limpio.join('\n')); }}catch(e){{}}
    }}
    function quitarDniLote(dni){{
      const d = soloDni(dni);
      const detalle = getLoteDetalle();
      delete detalle[d];
      const arr = getLoteArray().filter(x => x !== d);
      setLoteArray(arr, detalle);
      avisoMovil('DNI quitado del lote: ' + dni, false);
      setTimeout(()=>document.getElementById('dni_consumo')?.focus(), 100);
    }}
    function limpiarLoteConsumos(){{
      setLoteArray([], {{}});
      try{{ if(sessionStorage.getItem('limpiar_lote_tras_envio') === '1'){{ localStorage.removeItem('lote_consumos_' + new Date().toISOString().slice(0,10)); localStorage.removeItem('lote_consumos_detalle_' + new Date().toISOString().slice(0,10)); sessionStorage.removeItem('limpiar_lote_tras_envio'); }} }}catch(e){{}}
      const inp = document.getElementById('dni_consumo');
      const out = document.getElementById('nombre_trabajador');
      if(inp) inp.value='';
      if(out) out.value='';
      avisoMovil('Lote temporal limpiado.', false);
      setTimeout(()=>inp?.focus(), 100);
    }}
    function beepOk(){{
      try{{
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        const ctx = new AudioCtx();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.frequency.value = 880; gain.gain.value = 0.07;
        osc.start(); setTimeout(()=>{{osc.stop(); ctx.close();}}, 140);
      }}catch(e){{}}
      if(navigator.vibrate) navigator.vibrate(90);
    }}
    function avisoMovil(msg, ok=true){{
      const div = document.createElement('div');
      div.textContent = msg;
      div.style.position='fixed'; div.style.left='12px'; div.style.right='12px'; div.style.bottom='18px';
      div.style.zIndex='99999'; div.style.padding='12px 14px'; div.style.borderRadius='12px';
      div.style.fontWeight='900'; div.style.color='white'; div.style.textAlign='center';
      div.style.background = ok ? '#17a34a' : '#b91c1c';
      document.body.appendChild(div); setTimeout(()=>div.remove(), 1900);
    }}
    async function validarDni(dni){{
      dni = soloDni(dni);
      if(dni.length !== 8) return {{ok:false, msg:'DNI incompleto'}};
      const r = await fetch('/api/trabajador/' + encodeURIComponent(dni), {{cache:'no-store'}});
      return await r.json();
    }}
    async function buscarTrabajadorConsumo(force=false){{
      const inp = document.getElementById('dni_consumo');
      const out = document.getElementById('nombre_trabajador');
      if(!inp || !out) return;
      const dni = soloDni(inp.value);
      if(inp.value !== dni) inp.value = dni;
      if(dni.length < 8){{ out.value=''; ultimoDniValidado=''; const info=document.getElementById('info_trabajador_consumo'); if(info){{info.style.display='none'; info.innerHTML='';}} return; }}
      if(!force && ultimoDniValidado === dni) return;
      ultimoDniValidado = dni;
      out.value = 'Validando DNI...';
      try{{
        const d = await validarDni(dni);
        if(d.ok){{
          out.value = d.nombre || '';
          out.title = d.nombre || '';
          const info = document.getElementById('info_trabajador_consumo');
          if(info){{
            info.style.display = 'block';
            info.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px"><div><b>Trabajador</b><br>' + (d.nombre || '-') + '</div><div><b>DNI</b><br>' + dni + '</div><div><b>Área</b><br>' + (d.area || '-') + '</div><div><b>Estado</b><br><span class="badge ok">Activo</span></div></div>';
          }}
          if(document.getElementById('modo_lote')?.checked){{
            setTimeout(()=>agregarDniLote(dni, d.nombre), 80);
          }}else{{
            beepOk();
          }}
        }}else{{
          out.value = 'DNI no encontrado';
          out.title = 'DNI no encontrado';
          const info = document.getElementById('info_trabajador_consumo');
          if(info){{ info.style.display='block'; info.innerHTML='<span style="color:#991b1b">DNI no encontrado en Trabajadores: ' + dni + '</span>'; }}
          if(document.getElementById('modo_lote')?.checked) avisoMovil('DNI no encontrado: ' + dni, false);
        }}
      }}catch(e){{ out.value='No se pudo validar DNI'; avisoMovil('Error validando DNI.', false); }}
    }}
    function dniInputHandler(){{
      const inp = document.getElementById('dni_consumo');
      if(inp) inp.value = soloDni(inp.value);
      clearTimeout(dniTimer);
      const espera = (inp && inp.value.length === 8) ? 30 : 130;
      dniTimer = setTimeout(()=>buscarTrabajadorConsumo(false), espera);
    }}
    function agregarDniLote(dni, nombre){{
      dni = soloDni(dni);
      if(dni.length !== 8) return;
      const arr = getLoteArray();
      const detalle = getLoteDetalle();
      if(nombre) detalle[dni] = nombre;
      if(arr.includes(dni)){{
        setLoteArray(arr, detalle);
        avisoMovil('DNI ya estaba guardado en el lote: ' + dni, false);
      }}else{{
        arr.push(dni);
        setLoteArray(arr, detalle);
        beepOk();
        avisoMovil('DNI guardado y visualizado en lote: ' + dni + (nombre ? ' - ' + nombre : ''), true);
      }}
      const inp = document.getElementById('dni_consumo');
      const out = document.getElementById('nombre_trabajador');
      const info = document.getElementById('info_trabajador_consumo');
      if(inp) inp.value = '';
      if(out) out.value = '';
      if(info){{ info.style.display='block'; info.innerHTML='<b>Lote activo:</b> ' + getLoteArray().length + ' DNI(s) guardados y visibles en el cuadro temporal. Presiona <b>REGISTRO DE CONSUMO</b> para registrar todo.'; }}
      ultimoDniValidado = '';
      setTimeout(()=>inp?.focus(), 120);
    }}
    async function agregarActualAlLote(){{
      const inp = document.getElementById('dni_consumo');
      const dni = soloDni(inp ? inp.value : '');
      if(dni.length !== 8){{ avisoMovil('Digite o escanee un DNI válido de 8 dígitos.', false); return; }}
      try{{
        const d = await validarDni(dni);
        if(d.ok) agregarDniLote(dni, d.nombre || 'Trabajador validado');
        else avisoMovil('DNI no encontrado: ' + dni, false);
      }}catch(e){{ avisoMovil('No se pudo validar el DNI.', false); }}
    }}
    function toggleLote(){{
      const on = document.getElementById('modo_lote')?.checked;
      const box = document.getElementById('dni_lote');
      const panel = document.getElementById('lote_panel');
      const dni = document.getElementById('dni_consumo');
      if(box) box.style.display = 'none';
      if(panel) panel.style.display = on ? 'block' : 'none';
      if(dni) dni.required = !on;
      setLoteArray(getLoteArray());
      // Al activar el check después de digitar un DNI válido, lo pasamos al espacio temporal.
      if(on){{
        const actual = soloDni(dni ? dni.value : '');
        const nombreActual = document.getElementById('nombre_trabajador')?.value || '';
        if(actual.length === 8 && !/no encontrado|validando|error/i.test(nombreActual)){{
          setTimeout(()=>agregarDniLote(actual, nombreActual), 60);
        }}
      }}
      const btn = document.getElementById('btn_submit_consumo');
      if(btn) btn.textContent = 'REGISTRO DE CONSUMO';
      if(on) avisoMovil('Registro masivo activado. Los DNI se guardarán en lote temporal hasta presionar REGISTRO DE CONSUMO.', true);
    }}
    async function procesarDniQR(texto){{
      if(scannerBusy) return;
      const dni = soloDni(texto);
      if(dni.length !== 8){{ avisoMovil('QR/barras inválido: no contiene DNI de 8 dígitos.', false); return; }}
      const ahoraScan = Date.now();
      if(document.getElementById('modo_lote')?.checked && dni === ultimoScanDni && (ahoraScan - ultimoScanTs) < 2500) return;
      ultimoScanDni = dni; ultimoScanTs = ahoraScan;
      scannerBusy = true;
      const inp = document.getElementById('dni_consumo');
      const out = document.getElementById('nombre_trabajador');
      if(inp) inp.value = dni;
      try{{
        const d = await validarDni(dni);
        if(d.ok){{
          if(out) out.value = d.nombre || '';
          const info = document.getElementById('info_trabajador_consumo');
          if(info){{ info.style.display='block'; info.innerHTML='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px"><div><b>Trabajador</b><br>' + (d.nombre || '-') + '</div><div><b>DNI</b><br>' + dni + '</div><div><b>Área</b><br>' + (d.area || '-') + '</div><div><b>Estado</b><br><span class="badge ok">Activo</span></div></div>'; }}
          ultimoDniValidado = dni;
          if(document.getElementById('modo_lote')?.checked){{ agregarDniLote(dni, d.nombre); }}
          else {{ beepOk(); avisoMovil('DNI reconocido: ' + (d.nombre || dni), true); }}
        }}else{{
          if(out) out.value = 'DNI no encontrado';
          avisoMovil('DNI no encontrado: ' + dni, false);
        }}
      }}catch(e){{ avisoMovil('No se pudo validar el DNI.', false); }}
      setTimeout(()=>{{ scannerBusy=false; }}, document.getElementById('modo_lote')?.checked ? 350 : 900);
    }}
    async function abrirScannerQR(){{
      const cont = document.getElementById('qr-reader');
      if(!cont) return;
      if(location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1'){{
        avisoMovil('La cámara necesita HTTPS. Abre el enlace de Render con https://', false);
      }}
      cont.style.display='block';
      cont.innerHTML = `<div style="padding:10px;border:1px solid #dce6f0;border-radius:12px;background:#f8fbff">
        <b>Escáner con cámara activo</b><br>
        <div id="qr-reader-live" style="width:100%;max-width:430px;margin-top:8px"></div>
        <video id="qr-video-live" playsinline muted autoplay style="display:none;width:100%;max-width:430px;border-radius:12px;margin-top:8px;background:#000"></video>
        <canvas id="qr-canvas-live" style="display:none"></canvas>
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
          <button type="button" class="btn-red" onclick="cerrarScannerQR()">Cerrar cámara</button>
        </div>
        <small class="muted">Permite la cámara. En celular usa Chrome y el enlace HTTPS de Render.</small>
      </div>`;
      try{{
        if(window.Html5Qrcode){{
          const formatos = window.Html5QrcodeSupportedFormats ? [
            Html5QrcodeSupportedFormats.QR_CODE,
            Html5QrcodeSupportedFormats.CODE_128,
            Html5QrcodeSupportedFormats.CODE_39,
            Html5QrcodeSupportedFormats.EAN_13,
            Html5QrcodeSupportedFormats.EAN_8,
            Html5QrcodeSupportedFormats.ITF,
            Html5QrcodeSupportedFormats.UPC_A,
            Html5QrcodeSupportedFormats.UPC_E,
            Html5QrcodeSupportedFormats.PDF_417
          ].filter(Boolean) : undefined;
          qrActivo = new Html5Qrcode('qr-reader-live', formatos ? {{ formatsToSupport: formatos, verbose: false }} : undefined);
          await qrActivo.start(
            {{ facingMode: {{ ideal: 'environment' }} }},
            {{ fps: 15, qrbox: {{ width: 280, height: 180 }}, rememberLastUsedCamera: true }},
            async (decodedText) => {{ await procesarDniQR(decodedText); if(!document.getElementById('modo_lote')?.checked){{ cerrarScannerQR(); }} }},
            () => {{}}
          );
          const ce = document.getElementById('camara_estado_lote'); if(ce) ce.innerHTML = '<span class="cam-on">● encendida continua</span>';
          avisoMovil('Cámara activada. En modo masivo NO se apaga al detectar.', true);
          return;
        }}
      }}catch(e){{ console.warn('Html5Qrcode falló, usando respaldo:', e); }}
      try{{ await iniciarScannerNativo(); }}
      catch(e2){{ alert('No se pudo abrir la cámara. Usa HTTPS de Render, acepta permisos y prueba Chrome/Edge. Detalle: ' + (e2 && e2.message ? e2.message : e2)); }}
    }}
    async function iniciarScannerNativo(){{
      if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('El navegador no permite cámara.');
      const video = document.getElementById('qr-video-live');
      const canvas = document.getElementById('qr-canvas-live');
      const live = document.getElementById('qr-reader-live');
      if(live) live.innerHTML = '<b>Usando cámara directa...</b><br><small>Detecta QR con jsQR y barras con BarcodeDetector si el navegador lo soporta.</small>';
      const stream = await navigator.mediaDevices.getUserMedia({{video: {{facingMode: {{ideal:'environment'}}}}, audio:false}});
      qrActivo = {{stream: stream, stopped:false}};
      video.srcObject = stream; video.style.display='block';
      await video.play();
      let detector = null;
      if('BarcodeDetector' in window){{
        try{{ detector = new BarcodeDetector({{formats:['qr_code','code_128','code_39','ean_13','ean_8','itf','codabar','upc_a','upc_e','pdf417']}}); }}catch(e){{}}
      }}
      const ce = document.getElementById('camara_estado_lote'); if(ce) ce.innerHTML = '<span class="cam-on">● encendida continua</span>';
      avisoMovil('Cámara activada.', true);
      const loop = async () => {{
        if(!qrActivo || qrActivo.stopped) return;
        try{{
          if(detector){{
            const codes = await detector.detect(video);
            if(codes && codes.length){{
              await procesarDniQR(codes[0].rawValue || '');
              if(!document.getElementById('modo_lote')?.checked){{ cerrarScannerQR(); return; }}
            }}
          }}
          if(window.jsQR && video.videoWidth > 0){{
            canvas.width = video.videoWidth; canvas.height = video.videoHeight;
            const ctx = canvas.getContext('2d', {{willReadFrequently:true}});
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const code = jsQR(imageData.data, imageData.width, imageData.height);
            if(code && code.data){{
              await procesarDniQR(code.data);
              if(!document.getElementById('modo_lote')?.checked){{ cerrarScannerQR(); return; }}
            }}
          }}
        }}catch(e){{}}
        requestAnimationFrame(loop);
      }};
      requestAnimationFrame(loop);
    }}
    function cerrarScannerQR(){{
      try{{
        if(qrActivo){{
          if(typeof qrActivo.stop === 'function'){{
            qrActivo.stop().catch(()=>{{}}).finally(()=>{{ try{{ qrActivo.clear(); }}catch(e){{}} }});
          }}
          if(qrActivo.stream){{
            qrActivo.stopped = true;
            qrActivo.stream.getTracks().forEach(t => t.stop());
          }}
        }}
      }}catch(e){{}}
      qrActivo = null;
      const cont = document.getElementById('qr-reader');
      const ce = document.getElementById('camara_estado_lote'); if(ce) ce.textContent = 'apagada';
      if(cont){{ cont.style.display='none'; cont.innerHTML=''; }}
    }}
    function validarAntesEnviar(e){{
      const lote = document.getElementById('modo_lote')?.checked;
      if(lote){{
        let arr = getLoteArray();
        const actual = soloDni(document.getElementById('dni_consumo')?.value || '');
        const nombreActual = document.getElementById('nombre_trabajador')?.value || '';
        if(arr.length === 0 && actual.length === 8 && !/no encontrado|validando|error/i.test(nombreActual)){{
          arr = [actual];
          setLoteArray(arr);
        }}
        if(arr.length === 0){{ e.preventDefault(); avisoMovil('No hay DNI válidos guardados para el registro masivo.', false); return false; }}
        document.getElementById('dni_lote').value = arr.join('\n');
        if(!confirm('Se registrarán ' + arr.length + ' consumo(s) para la fecha de hoy. ¿Confirmas REGISTRO DE CONSUMO?')){{ e.preventDefault(); return false; }}
      }}
      try{{ sessionStorage.setItem('limpiar_lote_tras_envio', '1'); }}catch(ex){{}}
      return true;
    }}
    document.addEventListener('DOMContentLoaded', ()=>{{
      const inp = document.getElementById('dni_consumo');
      if(inp){{
        inp.addEventListener('paste', ()=>setTimeout(dniInputHandler, 40));
        inp.addEventListener('input', dniInputHandler);
        inp.addEventListener('keyup', dniInputHandler);
        inp.addEventListener('change', dniInputHandler);
        inp.addEventListener('keydown', (e)=>{{ if(e.key === 'Enter'){{ e.preventDefault(); buscarTrabajadorConsumo(true); }}}});
        setTimeout(()=>inp.focus(), 300);
      }}
      const form = document.getElementById('form_consumo');
      if(form) form.addEventListener('submit', validarAntesEnviar);
      try{{
        const key = 'lote_consumos_' + new Date().toISOString().slice(0,10);
        const guardado = localStorage.getItem(key);
        if(guardado && document.getElementById('dni_lote')) document.getElementById('dni_lote').value = guardado;
        const detGuardado = localStorage.getItem('lote_consumos_detalle_' + new Date().toISOString().slice(0,10));
        if(detGuardado && document.getElementById('lote_detalle')) document.getElementById('lote_detalle').value = detGuardado;
      }}catch(e){{}}
      toggleLote();
      setLoteArray(getLoteArray());
    }});
    </script>

    <br>
    {filtros}

    <div class="card">
      <div class="table-head">
        <h3>Consumos de la fecha {fecha_peru_txt(fecha)}</h3>
        <a class="btn btn-blue" href="{url_for('exportar_consumos')}">Exportar Excel</a>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>Fecha</th><th>Hora</th><th>DNI</th><th>Trabajador</th><th>Área</th><th>Tipo</th><th>Comedor</th><th>Fundo</th><th>Responsable</th><th>Cant.</th><th>P. Unit.</th><th>Total</th><th>Estado</th><th>Quitar</th></tr>
          {tabla}
        </table>
      </div>
    </div>
    """
    return render_page(html, "consumos")



@app.route("/quitar_consumo", methods=["POST"])
@login_required
@roles_required("admin", "rrhh", "comedor")
def quitar_consumo():
    id_ = request.form.get("id")
    c = q_one("SELECT * FROM consumos WHERE id=?", (id_,))
    if not c:
        flash("Consumo no encontrado.", "error")
        return redirect(url_for("consumos"))

    # NIVEL PRO: si ya está ENTREGADO, solo el administrador puede quitarlo.
    if c["estado"] == "ENTREGADO" and session.get("role") != "admin":
        audit_event("INTENTO_QUITAR_ENTREGADO_BLOQUEADO", "consumos", id_, f"DNI {c['dni']} - creado_por {c['creado_por']}")
        flash("Bloqueado: el pedido ya fue ENTREGADO. Solo un administrador puede quitarlo.", "error")
        return redirect(request.referrer or url_for("consumos"))

    clave = request.form.get("clave")
    if session.get("role") != "admin" and not require_remove_key(clave):
        flash("Clave incorrecta. No se quitó el consumo.", "error")
        return redirect(request.referrer or url_for("consumos"))

    if session.get("role") != "admin" and c["creado_por"] != session.get("user"):
        flash("Solo puedes quitar consumos registrados por tu usuario. El administrador puede quitar todos.", "error")
        return redirect(request.referrer or url_for("consumos"))

    audit_event("QUITAR_CONSUMO", "consumos", id_, f"DNI {c['dni']} - estado {c['estado']} - total {c['total']}")
    q_exec("DELETE FROM consumos WHERE id=?", (id_,))
    flash("Consumo quitado correctamente.", "ok")
    return redirect(request.referrer or url_for("consumos"))


@app.route("/api/entregas_pedidos")
@login_required
@roles_required("admin", "rrhh", "comedor")
def api_entregas_pedidos():
    fecha = request.args.get("fecha") or hoy_iso()
    dni = clean_dni(request.args.get("dni"))
    if dni:
        rows = q_all("SELECT * FROM consumos WHERE fecha=? AND dni=? ORDER BY hora,id", (fecha, dni))
    else:
        rows = q_all("SELECT * FROM consumos WHERE fecha=? ORDER BY CASE estado WHEN 'PENDIENTE' THEN 0 ELSE 1 END, hora, id", (fecha,))
    pedidos = []
    for i, r in enumerate(rows, 1):
        pedidos.append({
            "id": r["id"], "n": i, "hora": r["hora"], "tipo": r["tipo"],
            "cantidad": r["cantidad"], "observacion": r["observacion"] or "-",
            "estado": r["estado"], "pendiente": r["estado"] == "PENDIENTE"
        })
    return jsonify({"ok": True, "pedidos": pedidos, "count": len(pedidos)})

@app.route("/entregas", methods=["GET", "POST"])
@login_required
@roles_required("admin", "rrhh", "comedor")
def entregas():
    fecha = request.values.get("fecha") or hoy_iso()
    dni = clean_dni(request.values.get("dni"))

    if request.method == "POST":
        if dia_cerrado(fecha):
            flash("Día cerrado. No se pueden entregar más pedidos.", "error")
            return redirect(url_for("entregas", fecha=fecha, dni=dni))
        ids = request.form.getlist("ids")
        if request.form.get("entregar_todos") == "1":
            if dni:
                ids = [str(r["id"]) for r in q_all("SELECT id FROM consumos WHERE fecha=? AND dni=? AND estado='PENDIENTE'", (fecha, dni))]
            else:
                ids = [str(r["id"]) for r in q_all("SELECT id FROM consumos WHERE fecha=? AND estado='PENDIENTE'", (fecha,))]
        if not ids:
            flash("No hay pedidos pendientes seleccionados para entregar.", "error")
            return redirect(url_for("entregas", dni=dni, fecha=fecha))
        for id_ in ids:
            q_exec("UPDATE consumos SET estado='ENTREGADO', entregado_por=?, entregado_en=? WHERE id=? AND estado='PENDIENTE'",
                   (session["user"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), id_))
            audit_event("ENTREGAR_PEDIDO", "consumos", id_, f"DNI {dni}")
        flash(f"Pedidos entregados: {len(ids)}", "ok")
        return redirect(url_for("entregas", dni=dni, fecha=fecha))

    trabajador = q_one("SELECT * FROM trabajadores WHERE dni=? AND activo=1", (dni,)) if dni else None
    pedidos = q_all("SELECT * FROM consumos WHERE fecha=? AND dni=? ORDER BY hora,id", (fecha, dni)) if dni else q_all("SELECT * FROM consumos WHERE fecha=? ORDER BY CASE estado WHEN 'PENDIENTE' THEN 0 ELSE 1 END, hora,id", (fecha,))

    info = ""
    if dni and trabajador:
        info = f"""
        <div class="card" style="margin-top:12px;padding:14px">
          <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:15px">
            <div><b>Trabajador</b><br>{trabajador['nombre']}</div>
            <div><b>Área</b><br>{trabajador['area']}</div>
            <div><b>Estado</b><br><span class="badge ok">Activo</span></div>
          </div>
        </div>
        """
    elif dni:
        info = '<div class="flash error">DNI no encontrado o trabajador inactivo.</div>'

    tabla = "".join([
        f"""
        <tr>
          <td><input type="checkbox" name="ids" value="{r['id']}" {'disabled' if r['estado']!='PENDIENTE' else 'checked'}></td>
          <td>{i}</td><td>{r['hora']}</td><td>{r['tipo']}</td><td>{r['cantidad']}</td>
          <td>{r['observacion'] or '-'}</td>
          <td><span class="badge {'ok' if r['estado']=='ENTREGADO' else 'warn'}">{r['estado']}</span></td>
        </tr>
        """ for i, r in enumerate(pedidos, 1)
    ]) or "<tr><td colspan='7'>Sin pedidos para este DNI hoy.</td></tr>"

    html = topbar("Entrega de Pedidos", "Valida el DNI y entrega los pedidos del día") + f"""
    <div class="card">
      <form method="get" class="form-grid two">
        <input type="date" id="fecha_entrega" name="fecha" value="{fecha}">
        <input id="dni_entrega" name="dni" value="{dni}" placeholder="DNI del trabajador" autofocus>
        <button class="btn-blue">Buscar</button>
        <button type="button" class="btn-blue" onclick="refrescarEntregas()">🔄 Actualizar / refrescar</button>
      </form>
      {info}
    </div>

    <br>
    <div class="card">
      <div class="table-head">
        <h3>Pedidos del día ({fecha_peru_txt(fecha)})</h3>
        <span id="contador_pedidos" class="badge ok">{len(pedidos)} pedido(s)</span>
      </div>
      <form method="post">
        <input type="hidden" name="fecha" value="{fecha}">
        <input type="hidden" name="dni" value="{dni}">
        <div class="table-wrap">
          <table>
            <thead><tr><th></th><th>#</th><th>Hora</th><th>Tipo</th><th>Cantidad</th><th>Observación</th><th>Estado</th></tr></thead>
            <tbody id="pedidos_body">{tabla}</tbody>
          </table>
        </div>
        <br>
        <button name="entregar_seleccionado" value="1">Entregar seleccionado</button>
        <button name="entregar_todos" value="1" class="btn-blue">Entregar todos</button>
      </form>
      <p class="muted small">Actualización automática activa cada 5 segundos. También puedes usar el botón Actualizar / refrescar.</p>
    </div>
    <script>
    async function refrescarEntregas(){{
      const dni = document.getElementById('dni_entrega')?.value || '';
      const fecha = document.getElementById('fecha_entrega')?.value || '';
      // Sin DNI: muestra todos los pedidos del día. Con DNI: filtra solo ese trabajador.
      try{{
        const res = await fetch(`/api/entregas_pedidos?dni=${{encodeURIComponent(dni)}}`);
        const data = await res.json();
        const body = document.getElementById('pedidos_body');
        const contador = document.getElementById('contador_pedidos');
        if(contador) contador.textContent = `${{data.count}} pedido(s)`;
        if(!body) return;
        if(!data.pedidos || data.pedidos.length === 0){{
          body.innerHTML = `<tr><td colspan="7">Sin pedidos para este DNI hoy.</td></tr>`;
          return;
        }}
        body.innerHTML = data.pedidos.map(p => `
          <tr>
            <td><input type="checkbox" name="ids" value="${{p.id}}" ${{p.pendiente ? 'checked' : 'disabled'}}></td>
            <td>${{p.n}}</td><td>${{p.hora}}</td><td>${{p.tipo}}</td><td>${{p.cantidad}}</td>
            <td>${{p.observacion}}</td>
            <td><span class="badge ${{p.estado === 'ENTREGADO' ? 'ok' : 'warn'}}">${{p.estado}}</span></td>
          </tr>`).join('');
      }}catch(e){{ console.warn('No se pudo refrescar entregas', e); }}
    }}
    setInterval(refrescarEntregas, 5000);
    </script>
    """
    return render_page(html, "entregas")

@app.route("/carga_masiva", methods=["GET", "POST"])
@login_required
@roles_required("admin", "rrhh", "comedor")
def carga_masiva():
    if request.method == "POST":
        if dia_cerrado():
            flash("Día cerrado. No se puede cargar consumos.", "error")
            return redirect(url_for("carga_masiva"))

        f = request.files.get("excel")
        if not f or not f.filename.lower().endswith((".xlsx", ".xls")):
            flash("Sube un archivo Excel válido.", "error")
            return redirect(url_for("carga_masiva"))

        try:
            df = pd.read_excel(f, dtype=str, engine="openpyxl" if f.filename.lower().endswith(".xlsx") else None).fillna("")
            df.columns = normalize_columns(df.columns)
        except Exception:
            flash("No se pudo leer el Excel. Guarda el archivo como .xlsx y vuelve a cargarlo.", "error")
            return redirect(url_for("carga_masiva"))

        if "DNI" not in df.columns:
            flash("Falta la columna DNI. Usa la plantilla.", "error")
            return redirect(url_for("carga_masiva"))

        total = len(df)
        creados = 0
        errores = 0

        for _, r in df.iterrows():
            dni = clean_dni(col_value(r, "DNI"))
            trabajador = q_one("SELECT * FROM trabajadores WHERE dni=? AND activo=1", (dni,))
            if not trabajador:
                errores += 1
                continue

            fecha_raw = clean_text(r.get("FECHA"))
            if fecha_raw:
                try:
                    fecha = pd.to_datetime(fecha_raw).date().isoformat()
                except Exception:
                    fecha = hoy_iso()
            else:
                fecha = hoy_iso()

            if dia_cerrado(fecha):
                errores += 1
                continue

            if q_one("SELECT id FROM consumos WHERE fecha=? AND dni=? AND COALESCE(adicional,0)=0", (fecha, dni)):
                errores += 1
                continue

            tipo = clean_text(r.get("TIPO")) or "Almuerzo"
            if tipo not in ["Almuerzo", "Dieta"]:
                tipo = "Almuerzo"
            comedor = clean_text(r.get("COMEDOR")) or "Comedor 01"
            fundo = clean_text(r.get("FUNDO")) or "Kawsay Allpa"
            responsable = clean_text(r.get("RESPONSABLE"))
            cantidad = int(float(r.get("CANTIDAD") or 1))
            precio = float(r.get("PRECIO_UNITARIO") or r.get("PRECIO") or 10)
            obs = clean_text(r.get("OBSERVACION"))
            q_exec("""
                INSERT INTO consumos(fecha,hora,dni,trabajador,empresa,area,tipo,cantidad,precio_unitario,total,observacion,comedor,fundo,responsable,adicional,estado,creado_por)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (fecha, hora_now(), dni, trabajador["nombre"], trabajador["empresa"], trabajador["area"], tipo, cantidad, precio, cantidad*precio, obs, comedor, fundo, responsable, 0, "PENDIENTE", session["user"]))
            creados += 1

        q_exec("INSERT INTO importaciones(archivo,total,creados,errores,usuario) VALUES(?,?,?,?,?)",
               (f.filename, total, creados, errores, session["user"]))
        flash(f"Carga terminada: {creados} creados, {errores} errores.", "ok" if errores == 0 else "error")
        return redirect(url_for("carga_masiva"))

    hist = q_all("SELECT * FROM importaciones ORDER BY id DESC LIMIT 10")
    tabla = "".join([
        f"<tr><td>{r['fecha_hora']}</td><td>{r['archivo']}</td><td>{r['total']}</td><td>{r['creados']}</td><td>{r['errores']}</td><td>{r['usuario']}</td><td>⬇️</td></tr>"
        for r in hist
    ]) or "<tr><td colspan='7'>Sin historial de importaciones.</td></tr>"

    html = topbar("Carga Masiva de Consumos", "Importa consumos desde un archivo Excel") + f"""
    <div class="card">
      <form method="post" enctype="multipart/form-data">
        <input type="file" name="excel" accept=".xlsx,.xls" required>
        <br><br>
        <button class="btn-orange">Importar consumos</button>
        <a class="btn btn-blue" href="{url_for('plantilla_consumos')}">Descargar plantilla Excel</a>
      </form>
    </div>

    <br>
    <div class="card">
      <h3 style="margin-top:0">Historial de importaciones</h3>
      <div class="table-wrap">
        <table>
          <tr><th>Fecha</th><th>Archivo</th><th>Total</th><th>Creados</th><th>Errores</th><th>Usuario</th><th></th></tr>
          {tabla}
        </table>
      </div>
    </div>
    """
    return render_page(html, "carga")


@app.route("/trabajadores", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def trabajadores():
    if request.method == "POST" and request.form.get("manual") == "1":
        dni = clean_dni(request.form.get("dni"))
        nombre = clean_text(request.form.get("nombre"))
        empresa = clean_text(request.form.get("empresa")) or "PRIZE"
        cargo = clean_text(request.form.get("cargo"))
        area = clean_text(request.form.get("area"))
        if len(dni) != 8 or not nombre:
            flash("Ingresa un DNI de 8 dígitos y nombre válido.", "error")
            return redirect(url_for("trabajadores"))

        existe = q_one("SELECT id FROM trabajadores WHERE dni=?", (dni,))
        if existe:
            q_exec("UPDATE trabajadores SET empresa=?,nombre=?,cargo=?,area=?,activo=1,actualizado=CURRENT_TIMESTAMP WHERE dni=?",
                   (empresa, nombre, cargo, area, dni))
        else:
            q_exec("INSERT INTO trabajadores(empresa,dni,nombre,cargo,area,activo) VALUES(?,?,?,?,?,1)",
                   (empresa, dni, nombre, cargo, area))
        flash("Trabajador guardado correctamente.", "ok")
        return redirect(url_for("trabajadores"))

    if request.method == "POST" and "excel" in request.files:
        f = request.files.get("excel")
        try:
            if not f or not f.filename:
                flash("Selecciona un archivo Excel para importar.", "error")
                return redirect(url_for("trabajadores"))
            if not f.filename.lower().endswith((".xlsx", ".xls")):
                flash("Sube un archivo Excel válido (.xlsx o .xls).", "error")
                return redirect(url_for("trabajadores"))

            registros_dict, total_filas, omitidos = leer_trabajadores_excel_stream(f)

            if not registros_dict:
                flash("No se importó nada: no encontré filas válidas con DNI de 8 dígitos y NOMBRE. Descarga la plantilla y vuelve a intentar.", "error")
                return redirect(url_for("trabajadores"))

            # REEMPLAZO TOTAL OPTIMIZADO:
            # Carga TODO el Excel válido, pero en una sola transacción y por lotes.
            # Esto evita que Render mate el proceso por abrir miles de conexiones.
            creados = reemplazar_trabajadores_batch(list(registros_dict.values()))

            q_exec("INSERT INTO importaciones(archivo,total,creados,errores,usuario) VALUES(?,?,?,?,?)",
                   (f.filename, total_filas, creados, omitidos, session.get("user", "")))
            flash(f"Base de trabajadores reemplazada correctamente: {creados} trabajadores cargados desde todo el Excel. Omitidos: {omitidos}.", "ok")
            return redirect(url_for("trabajadores"))
        except Exception as e:
            app.logger.exception("Error importando trabajadores")
            flash("No se pudo importar trabajadores. Usa la plantilla .xlsx y verifica columnas: EMPRESA, DNI, NOMBRE, CARGO, AREA. Detalle: " + str(e)[:180], "error")
            return redirect(url_for("trabajadores"))

    buscar = clean_text(request.args.get("buscar"))
    total_activos = q_one("SELECT COUNT(*) AS total FROM trabajadores WHERE activo=1")
    total_activos = int(total_activos["total"] if total_activos else 0)
    total_inactivos = q_one("SELECT COUNT(*) AS total FROM trabajadores WHERE activo=0")
    total_inactivos = int(total_inactivos["total"] if total_inactivos else 0)
    if buscar:
        b = f"%{buscar}%"
        rows = q_all("""
            SELECT * FROM trabajadores
            WHERE dni LIKE ? OR nombre LIKE ? OR cargo LIKE ? OR area LIKE ? OR empresa LIKE ?
            ORDER BY nombre LIMIT 1200
        """, (b, b, b, b, b))
    else:
        rows = q_all("SELECT * FROM trabajadores ORDER BY nombre LIMIT 1200")

    tabla = "".join([
        f"<tr><td>{r['empresa']}</td><td>{r['dni']}</td><td>{r['nombre']}</td><td>{r['cargo']}</td><td>{r['area']}</td><td><span class='badge ok'>Activo</span></td></tr>"
        for r in rows
    ]) or "<tr><td colspan='6'>Sin trabajadores encontrados.</td></tr>"

    html = topbar("Trabajadores", "Base de trabajadores activos para validar DNI") + f"""
    <div class="kpi-grid" style="grid-template-columns:repeat(3,minmax(180px,1fr))!important">
      <div class="card kpi-card">
        <div class="icon-circle ic-green">👥</div>
        <div>
          <div class="label">Trabajadores activos</div>
          <div class="num">{total_activos}</div>
          <div class="sub">Disponibles para validar DNI</div>
        </div>
      </div>
      <div class="card kpi-card">
        <div class="icon-circle ic-blue">🔎</div>
        <div>
          <div class="label">Resultado mostrado</div>
          <div class="num">{len(rows)}</div>
          <div class="sub">Según filtro actual</div>
        </div>
      </div>
      <div class="card kpi-card">
        <div class="icon-circle ic-orange">⛔</div>
        <div>
          <div class="label">Inactivos</div>
          <div class="num">{total_inactivos}</div>
          <div class="sub">No validan consumo</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Registro manual</h3>
      <form method="post" class="form-grid" id="form_consumo" onsubmit="return validarAntesEnviar(event)">
        <input type="hidden" name="manual" value="1">
        <input name="empresa" value="PRIZE" placeholder="Empresa">
        <input name="dni" placeholder="DNI" required>
        <input name="nombre" placeholder="Apellidos y nombres" required>
        <input name="cargo" placeholder="Cargo">
        <input name="area" placeholder="Área">
        <button>Guardar</button>
      </form>
    </div>

    <br>
    <div class="card">
      <h3 style="margin-top:0">Carga masiva trabajadores</h3>
      <p class="muted small"><b>Importante:</b> al importar, la base de trabajadores se REEMPLAZA por la información del Excel.</p>
      <form method="post" enctype="multipart/form-data" class="form-grid">
        <input type="file" name="excel" accept=".xlsx,.xls" required>
        <button class="btn-orange">Importar y reemplazar trabajadores</button>
        <a class="btn btn-blue" href="{url_for('plantilla_trabajadores')}">Descargar plantilla</a>
      </form>
    </div>

    <br>
    <div class="card">
      <div class="table-head">
        <h3>Base de trabajadores</h3>
      </div>

      <form method="get" action="{url_for('trabajadores')}" class="form-grid" style="grid-template-columns:1fr auto auto;margin-bottom:14px">
        <input name="buscar" value="{buscar}" placeholder="Buscar por DNI, nombre, cargo, área o empresa">
        <button class="btn-blue">Buscar</button>
        <a class="btn" href="{url_for('trabajadores')}">Actualizar</a>
      </form>

      <div class="table-wrap">
        <table>
          <tr><th>Empresa</th><th>DNI</th><th>Nombre</th><th>Cargo</th><th>Área</th><th>Estado</th></tr>
          {tabla}
        </table>
      </div>
    </div>
    """
    return render_page(html, "trabajadores")


@app.route("/cierre_dia", methods=["GET", "POST"])
@login_required
@roles_required("admin", "comedor", "rrhh")
def cierre_dia():
    fecha = hoy_iso()
    cerrado = dia_cerrado(fecha)

    if request.method == "POST":
        if cerrado:
            flash("Este día ya fue cerrado.", "error")
            return redirect(url_for("cierre_dia"))

        correo = clean_text(request.form.get("correo"))
        pedidos = q_all("SELECT * FROM consumos WHERE fecha=? ORDER BY area,trabajador", (fecha,))
        df = pd.DataFrame([dict(p) for p in pedidos])
        if df.empty:
            df = pd.DataFrame(columns=["fecha","hora","dni","trabajador","empresa","area","tipo","cantidad","precio_unitario","total","estado","creado_por"])

        resumen_area = df.groupby(["area","estado"], as_index=False).agg(cantidad=("cantidad","sum"), total=("total","sum")) if not df.empty else pd.DataFrame()
        resumen_usuario = df.groupby(["creado_por"], as_index=False).agg(consumos=("dni","count"), total=("total","sum")) if not df.empty else pd.DataFrame()

        filename = f"cierre_comedor_{fecha.replace('-','_')}.xlsx"
        path = os.path.join(REPORT_DIR, filename)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="DETALLE_DIA", index=False)
            resumen_area.to_excel(writer, sheet_name="RESUMEN_AREA", index=False)
            resumen_usuario.to_excel(writer, sheet_name="RESUMEN_USUARIOS", index=False)

        total_consumos = len(pedidos)
        total_entregados = sum(1 for p in pedidos if p["estado"] == "ENTREGADO")
        total_pendientes = total_consumos - total_entregados
        total_importe = sum(float(p["total"] or 0) for p in pedidos)

        estado_correo = send_report_email(
            correo,
            f"Cierre comedor PRIZE {fecha_peru_txt(fecha)}",
            f"Se adjunta cierre del día. Consumos: {total_consumos}. Entregados: {total_entregados}. Pendientes: {total_pendientes}. Total: {money(total_importe)}",
            path
        )

        q_exec("""
            INSERT INTO cierres(fecha,cerrado_por,total_consumos,total_entregados,total_pendientes,total_importe,archivo_excel,correo_destino,correo_estado)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (fecha, session["user"], total_consumos, total_entregados, total_pendientes, total_importe, filename, correo, estado_correo))

        flash(f"Día cerrado. Reporte generado: {filename}. Correo: {estado_correo}", "ok")
        return redirect(url_for("cierre_dia"))

    stats = q_one("""
        SELECT COUNT(*) c, COALESCE(SUM(total),0) t,
        SUM(CASE WHEN estado='ENTREGADO' THEN 1 ELSE 0 END) e
        FROM consumos WHERE fecha=?
    """, (fecha,))
    usuarios = q_all("SELECT creado_por, COUNT(*) c, COALESCE(SUM(total),0) t FROM consumos WHERE fecha=? GROUP BY creado_por", (fecha,))
    ultimo = q_one("SELECT hora FROM consumos WHERE fecha=? ORDER BY hora DESC,id DESC LIMIT 1", (fecha,))
    cerrado_html = ""
    if cerrado:
        cerrado_html = f"""
        <div class="card">
          <span class="badge off">DÍA CERRADO</span>
          <p>Archivo generado: <b>{cerrado['archivo_excel']}</b></p>
          <a class="btn btn-blue" href="{url_for('descargar_cierre', filename=cerrado['archivo_excel'])}">Descargar reporte</a>
        </div>
        """
    usuarios_html = "".join([
        f"<div class='user-row'><span>👤 <b>{u['creado_por'] or 'sin usuario'}</b></span><span>{u['c']} consumos</span><span>{money(u['t'])}</span></div>"
        for u in usuarios
    ]) or "<div class='muted'>Sin usuarios con registros hoy.</div>"

    form = "" if cerrado else f"""
    <form method="post">
      <label><b>Correo destino</b></label><br><br>
      <input name="correo" value="{os.getenv('REPORTE_DESTINO','administracion@prize.pe')}" placeholder="correo@empresa.com">
      <br><br>
      <label><input type="checkbox" checked> Incluir archivo Excel</label>
      <br><br>
      <button class="btn-orange" style="width:100%">Cerrar día y enviar reporte</button>
    </form>
    """

    admin_extra = ""
    if session.get("role") == "admin":
        admin_extra = f"""
        <div class='admin-actions'>
          <a class='btn btn-orange' href='{url_for('cerrar_dia_manual')}'>🔒 Cerrar día</a>
          <a class='btn btn-blue' href='{url_for('abrir_dia_manual')}'>🔓 Abrir día</a>
          <a class='btn' href='{url_for('exportar_concesionaria')}'>Archivo concesionaria</a>
          <a class='btn btn-orange' href='{url_for('reporte_entrega')}'>Reporte entrega/pago</a>
        </div>
        """
    html = topbar("Cierre de Día y Reportes", "Consolida y envía el reporte del día por correo") + admin_extra + f"""
    <div class="card">
      <span class="badge {'off' if cerrado else 'ok'}">🟢 {'DÍA CERRADO' if cerrado else 'DÍA ABIERTO'}</span>
      <span style="margin-left:18px" class="muted">Fecha actual: {fecha_peru_txt(fecha)}</span>

      <div class="mini-kpis">
        <div class="card"><span class="muted small">Total consumos</span><b>{stats['c']}</b></div>
        <div class="card"><span class="muted small">Total facturado</span><b>{money(stats['t'])}</b></div>
        <div class="card"><span class="muted small">Usuarios que registraron</span><b>{len(usuarios)}</b></div>
        <div class="card"><span class="muted small">Último registro</span><b>{ultimo['hora'] if ultimo else '--:--'}</b></div>
      </div>
    </div>

    <br>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
      <div class="card">
        <h3 style="margin-top:0">Usuarios que registraron hoy</h3>
        {usuarios_html}
      </div>
      <div class="card">
        <h3 style="margin-top:0">Enviar reporte por correo</h3>
        {form}
        {cerrado_html}
      </div>
    </div>
    """
    return render_page(html, "cierre")


@app.route("/reportes")
@login_required
def reportes():
    fecha_inicio = request.args.get("fecha_inicio") or request.args.get("fecha") or hoy_iso()
    fecha_fin = request.args.get("fecha_fin") or fecha_inicio
    buscar = clean_text(request.args.get("buscar"))

    cond, params = rango_sql(fecha_inicio, fecha_fin)
    where = cond
    final_params = list(params)

    if buscar:
        where += " AND (cerrado_por LIKE ? OR correo_estado LIKE ? OR archivo_excel LIKE ? OR correo_destino LIKE ?)"
        b = f"%{buscar}%"
        final_params += [b, b, b, b]

    rows = q_all(f"SELECT * FROM cierres WHERE {where} ORDER BY fecha DESC LIMIT 200", tuple(final_params))

    tabla = "".join([
        f"""
        <tr>
          <td>{fecha_peru_txt(r['fecha'])}</td>
          <td>{r['total_consumos']}</td>
          <td>{money(r['total_importe'])}</td>
          <td>{r['cerrado_por']}</td>
          <td>{r['correo_destino'] or '-'}</td>
          <td>{r['correo_estado'] or '-'}</td>
          <td>{('<a class="btn btn-blue" href="' + url_for('descargar_cierre', filename=r['archivo_excel']) + '">Descargar</a>') if r['archivo_excel'] else '-'}</td>
        </tr>
        """
        for r in rows
    ]) or "<tr><td colspan='7'>Sin reportes en el rango seleccionado.</td></tr>"

    html = topbar("Reportes", "Historial de cierres y reportes generados") + filtro_bar(url_for("reportes"), fecha_inicio, fecha_fin, buscar) + f"""
    <div class="card">
      <div class="table-head">
        <h3>Reportes generados</h3>
      </div>
      <div class="table-wrap">
        <table>
          <tr>
            <th>Fecha</th>
            <th>Consumos</th>
            <th>Total</th>
            <th>Cerrado por</th>
            <th>Correo destino</th>
            <th>Estado correo</th>
            <th>Archivo</th>
          </tr>
          {tabla}
        </table>
      </div>
    </div>
    """
    return render_page(html, "reportes")


@app.route("/configuracion", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def configuracion():
    if request.method == "POST":
        cfg_set("bloqueo_activo", "1" if request.form.get("bloqueo_activo") else "0")
        cfg_set("hora_inicio", request.form.get("hora_inicio") or "00:00")
        cfg_set("hora_fin", request.form.get("hora_fin") or "23:59")
        cfg_set("clave_quitar", request.form.get("clave_quitar") or "1234")
        flash("Configuración actualizada.", "ok")
        return redirect(url_for("configuracion"))

    usuarios = q_all("SELECT id, username, role, active, COALESCE(password_plain,'') AS password_plain FROM usuarios ORDER BY username")
    usuarios_html = "".join([
        f"<tr><td>{u['username']}</td><td>{u['role']}</td><td><span class='badge {'ok' if u['active'] else 'off'}'>{'Activo' if u['active'] else 'Bloqueado'}</span></td></tr>"
        for u in usuarios
    ])

    html = topbar("Configuración", "Bloqueo por horario, clave para quitar y usuarios") + f"""
    <div class="card">
      <h3 style="margin-top:0">Bloqueo de registro por horario</h3>
      <form method="post" class="form-grid" id="form_consumo" onsubmit="return validarAntesEnviar(event)">
        <label style="font-weight:900"><input type="checkbox" name="bloqueo_activo" {'checked' if cfg_get('bloqueo_activo','0')=='1' else ''}> Activar bloqueo para usuarios</label>
        <input type="time" name="hora_inicio" value="{cfg_get('hora_inicio','00:00')}">
        <input type="time" name="hora_fin" value="{cfg_get('hora_fin','23:59')}">
        <input name="clave_quitar" value="{cfg_get('clave_quitar','1234')}" placeholder="Clave para quitar consumo">
        <button>Guardar configuración</button>
      </form>
      <p class="muted small">Con bloqueo activo, los usuarios registran solo dentro del horario. Admin puede registrar adicionales.</p>
    </div>

    <br>
    <div class="card">
      <div class="table-head"><h3>Usuarios y claves</h3><a class="btn btn-blue" href="{url_for('usuarios_admin')}">Crear usuarios</a></div>
      <div class="table-wrap"><table><tr><th>Usuario</th><th>Rol</th><th>Estado</th></tr>{usuarios_html}</table></div>
    </div>
    """
    return render_page(html, "config")


@app.route("/usuarios", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def usuarios_admin():
    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        password = request.form.get("password") or ""
        role = asegurar_rol_usuario(request.form.get("role") or "comedor")
        active = 1 if request.form.get("active") else 0
        if not username or not password:
            flash("Usuario y clave son obligatorios.", "error")
            return redirect(url_for("usuarios_admin"))

        existe = q_one("SELECT id FROM usuarios WHERE username=?", (username,))
        if existe:
            q_exec("UPDATE usuarios SET password_hash=?, password_plain=?, role=?, active=? WHERE username=?",
                   (generate_password_hash(password), password, role, active, username))
            send_admin_user_notice(username, role, "actualizado")
            audit_event("USUARIO_ACTUALIZADO", "usuarios", username, f"Rol: {role}")
            flash("Usuario actualizado y guardado correctamente.", "ok")
        else:
            q_exec("INSERT INTO usuarios(username,password_hash,password_plain,role,active) VALUES(?,?,?,?,?)",
                   (username, generate_password_hash(password), password, role, active))
            send_admin_user_notice(username, role, "creado")
            audit_event("USUARIO_CREADO", "usuarios", username, f"Rol: {role}")
            flash("Usuario creado y guardado correctamente.", "ok")
        return redirect(url_for("usuarios_admin"))

    usuarios = q_all("SELECT id, username, role, active, COALESCE(password_plain,'') AS password_plain FROM usuarios ORDER BY id ASC")
    total_usuarios = len(usuarios)
    tabla = "".join([
        f"""
        <tr data-user-row data-user="{(u['username'] or '').lower()}" data-role="{(u['role'] or '').lower()}">
          <td>{i}</td>
          <td><b>{u['username']}</b></td>
          <td>{'Administrador total' if u['role']=='admin' else 'Usuario operativo'}</td>
          <td>
            <div class="pass-cell">
              <input class="pass-view" type="password" value="{u['password_plain'] or 'No registrada'}" readonly>
              <button type="button" class="eye-btn" onclick="togglePass(this)" title="Ver / ocultar contraseña">👁️</button>
            </div>
          </td>
          <td><span class='badge {'ok' if u['active'] else 'off'}'>{'Activo' if u['active'] else 'Bloqueado'}</span></td>
          <td>
            <form method='post' action='{url_for('eliminar_usuario', username=u['username'])}' onsubmit="return confirm('¿Eliminar este usuario?');" style='display:inline'>
              <button class='btn-orange' style='padding:8px 12px' {'disabled' if u['username'] in ['adm','adm1','adm2'] or u['username']==session.get('user') else ''}>Eliminar</button>
            </form>
          </td>
        </tr>
        """
        for i, u in enumerate(usuarios, 1)
    ])
    html = topbar("Crear usuarios y claves", "Solo administrador") + f"""
    <div class="card">
      <h3 style="margin-top:0">Crear / actualizar usuario</h3>
      <form method="post" class="form-grid" id="form_consumo" onsubmit="return validarAntesEnviar(event)">
        <input name="username" placeholder="Usuario" required>
        <input name="password" placeholder="Clave" required>
        <select name="role">
          <option value="comedor">Usuario operativo: Consumos / Entregas / Cerrar día</option>
          <option value="admin">Administrador total: crear y eliminar usuarios</option>
        </select>
        <label style="font-weight:900"><input type="checkbox" name="active" checked> Activo</label>
        <button>Guardar usuario</button>
      </form>
    </div>
    <br>
    <div class="card users-card">
      <div class="table-head" style="gap:14px;align-items:center;flex-wrap:wrap">
        <h3 style="margin:0">Usuarios registrados</h3>
        <span class="users-count">Total: {total_usuarios} usuario(s)</span>
        <input id="buscarUsuario" class="user-search" placeholder="🔎 Buscar usuario dinámicamente..." oninput="filtrarUsuarios()">
      </div>
      <div class="table-wrap users-scroll">
        <table id="tablaUsuarios">
          <tr><th>#</th><th>Usuario</th><th>Nivel</th><th>Contraseña</th><th>Estado</th><th>Acción</th></tr>{tabla}
        </table>
      </div>
      <p class="muted small">El usuario <b>adm</b> tiene clave <b>@123</b>. Los usuarios adm, adm1 y adm2 quedan como administradores totales.</p>
    </div>
    <script>
      function filtrarUsuarios(){{
        const q = (document.getElementById('buscarUsuario').value || '').toLowerCase().trim();
        let visibles = 0;
        document.querySelectorAll('[data-user-row]').forEach(tr => {{
          const texto = (tr.dataset.user + ' ' + tr.dataset.role + ' ' + tr.innerText.toLowerCase());
          const show = texto.includes(q);
          tr.style.display = show ? '' : 'none';
          if(show) visibles++;
        }});
        const badge = document.querySelector('.users-count');
        if(badge) badge.textContent = 'Total visible: ' + visibles + ' usuario(s)';
      }}
      function togglePass(btn){{
        const inp = btn.parentElement.querySelector('.pass-view');
        inp.type = inp.type === 'password' ? 'text' : 'password';
        btn.textContent = inp.type === 'password' ? '👁️' : '🙈';
      }}
    </script>
    """
    return render_page(html, "config")


@app.route("/usuarios/eliminar/<username>", methods=["POST"])
@login_required
@roles_required("admin")
def eliminar_usuario(username):
    username = clean_text(username)
    if username in ("adm", "adm1", "adm2"):
        flash("No se puede eliminar adm, adm1 ni adm2 porque son administradores principales.", "error")
        return redirect(url_for("usuarios_admin"))

    if username == session.get("user"):
        flash("No puedes eliminar el usuario con el que estás conectado.", "error")
        return redirect(url_for("usuarios_admin"))

    user = q_one("SELECT * FROM usuarios WHERE username=?", (username,))
    if not user:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("usuarios_admin"))

    if user["role"] == "admin":
        total_admins = q_one("SELECT COUNT(*) c FROM usuarios WHERE role='admin' AND active=1")["c"]
        if total_admins <= 2:
            flash("No se puede eliminar: deben quedar mínimo 2 administradores activos (adm1 y adm2).", "error")
            return redirect(url_for("usuarios_admin"))

    q_exec("DELETE FROM usuarios WHERE username=?", (username,))
    flash(f"Usuario {username} eliminado correctamente.", "ok")
    return redirect(url_for("usuarios_admin"))


# =========================
# DESCARGAS
# =========================
@app.route("/plantilla_consumos")
@login_required
def plantilla_consumos():
    df = pd.DataFrame([{
        "FECHA": hoy_iso(),
        "DNI": "74324033",
        "COMEDOR": "Comedor 01",
        "TIPO": "Almuerzo",
        "FUNDO": "Kawsay Allpa",
        "RESPONSABLE": "Nombre responsable",
        "CANTIDAD": 1,
        "PRECIO_UNITARIO": 10,
        "OBSERVACION": "Pedido desde Forms / QR DNI"
    }])
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="plantilla_carga_consumos.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/plantilla_trabajadores")
@login_required
def plantilla_trabajadores():
    df = pd.DataFrame([{
        "EMPRESA": "PRIZE",
        "DNI": "74324033",
        "NOMBRE": "AZABACHE LUJAN, OMAR EDUARDO",
        "CARGO": "OPERARIO",
        "AREA": "PRODUCCION"
    }])
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="plantilla_trabajadores.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/exportar_consumos")
@login_required
def exportar_consumos():
    rows = q_all("SELECT * FROM consumos ORDER BY fecha DESC,hora DESC")
    df = pd.DataFrame([dict(r) for r in rows])
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="consumos_comedor_prize.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/descargar_cierre/<path:filename>")
@login_required
def descargar_cierre(filename):
    safe = os.path.basename(filename)
    path = os.path.join(REPORT_DIR, safe)
    return send_file(path, as_attachment=True)


# =========================
# INICIO
# =========================
init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
