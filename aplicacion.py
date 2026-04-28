# -*- coding: utf-8 -*-
"""
Sistema Comedor PRIZE - Interfaz PRO
Archivo único app.py para Render / local.

Usuarios demo:
- admin / admin123
- rrhh / rrhh123
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
from io import BytesIO
from datetime import datetime, date
from functools import wraps
from email.message import EmailMessage

import pandas as pd
from flask import (
    Flask, request, redirect, url_for, session, send_file,
    render_template_string, flash
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

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(CONCESIONARIA_DIR, exist_ok=True)
os.makedirs(ENTREGAS_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "prize-comedor-pro-2026")


# =========================
# BASE DE DATOS SQLITE
# =========================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def q_all(sql, params=()):
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def q_one(sql, params=()):
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()


def q_exec(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
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


        # Migraciones consumo pro
        cols = [x["name"] for x in conn.execute("PRAGMA table_info(consumos)").fetchall()]
        for col, sqltype, default in [
            ("comedor", "TEXT", "'Comedor 01'"),
            ("fundo", "TEXT", "'Kawsay Allpa'"),
            ("responsable", "TEXT", "''"),
            ("adicional", "INTEGER", "0"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE consumos ADD COLUMN {col} {sqltype} DEFAULT {default}")

        defaults = {
            "bloqueo_activo": "0",
            "hora_inicio": "00:00",
            "hora_fin": "23:59",
            "clave_quitar": "1234",
        }
        for k, v in defaults.items():
            existe = conn.execute("SELECT clave FROM configuracion WHERE clave=?", (k,)).fetchone()
            if not existe:
                conn.execute("INSERT INTO configuracion(clave,valor) VALUES(?,?)", (k, v))

        # Elimina duplicados antiguos normales del mismo día/DNI dejando el primero.
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

        for username, password, role in [
            ("admin", "admin123", "admin"),
            ("rrhh", "rrhh123", "rrhh"),
            ("comedor", "comedor123", "comedor"),
        ]:
            existe = conn.execute("SELECT id FROM usuarios WHERE username=?", (username,)).fetchone()
            if not existe:
                conn.execute(
                    "INSERT INTO usuarios(username,password_hash,role,active) VALUES(?,?,?,1)",
                    (username, generate_password_hash(password), role),
                )

        # Datos demo para que la interfaz no salga vacía
        demos = [
            ("PRIZE", "74324033", "AZABACHE LUJAN, OMAR EDUARDO", "OPERARIO", "PRODUCCION"),
            ("PRIZE", "45148597", "CONCEPCION ZAVALETA, VICTOR", "OPERARIO", "PRODUCCION"),
            ("PRIZE", "47625779", "HUAYLLA NACARINO, RAUL", "OPERARIO", "PRODUCCION"),
            ("PRIZE", "41678684", "TANTALLEAN PINILLOS, ERNESTO", "OPERARIO", "PRODUCCION"),
            ("PRIZE", "80503598", "LLANOS VASQUEZ, SEGUNDO", "OPERARIO", "PRODUCCION"),
        ]
        for emp, dni, nom, cargo, area in demos:
            existe = conn.execute("SELECT id FROM trabajadores WHERE dni=?", (dni,)).fetchone()
            if not existe:
                conn.execute(
                    "INSERT INTO trabajadores(empresa,dni,nombre,cargo,area,activo) VALUES(?,?,?,?,?,1)",
                    (emp, dni, nom, cargo, area),
                )
        conn.commit()


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


def clean_dni(v):
    s = re.sub(r"\D", "", str(v or ""))
    if 1 <= len(s) < 8:
        return s.zfill(8)
    return s[:20]


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
        x = re.sub(r"\s+", "_", x)
        out.append(x)
    return out


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

</style>
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
        <a class="{{'on' if page=='dashboard'}}" href="{{url_for('dashboard')}}"><span class="nav-ico">📊</span>Dashboard</a>
        <a class="{{'on' if page=='consumos'}}" href="{{url_for('consumos')}}"><span class="nav-ico">🍽️</span>Consumos</a>
        <a class="{{'on' if page=='trabajadores'}}" href="{{url_for('trabajadores')}}"><span class="nav-ico">👥</span>Trabajadores</a>
        <a class="{{'on' if page=='entregas'}}" href="{{url_for('entregas')}}"><span class="nav-ico">🚚</span>Entregas <span class="pill nuevo">NUEVO</span></a>
        <a class="{{'on' if page=='reportes'}}" href="{{url_for('reportes')}}"><span class="nav-ico">📁</span>Reportes <span class="pill correo">CORREO</span></a>
        <a class="{{'on' if page=='cierre'}}" href="{{url_for('cierre_dia')}}"><span class="nav-ico">📁</span>Reportes Planilla</a>
        <a class="{{'on' if page=='carga'}}" href="{{url_for('carga_masiva')}}"><span class="nav-ico">📥</span>Carga Masiva</a>
        {% if session.get('role') == 'admin' %}
        <a class="{{'on' if page=='config'}}" href="{{url_for('configuracion')}}"><span class="nav-ico">⚙️</span>Config.</a>
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


@app.route("/consumos", methods=["GET", "POST"])
@login_required
@roles_required("admin", "rrhh", "comedor")
def consumos():
    if request.method == "POST":
        fecha = request.form.get("fecha") or hoy_iso()

        if dia_cerrado(fecha):
            flash("El día ya está cerrado. No se puede registrar consumos.", "error")
            return redirect(url_for("consumos"))

        bloqueado, msg = registro_bloqueado()
        if bloqueado and session.get("role") != "admin":
            flash(msg, "error")
            return redirect(url_for("consumos"))

        dni = clean_dni(request.form.get("dni"))
        trabajador = q_one("SELECT * FROM trabajadores WHERE dni=? AND activo=1", (dni,))
        if not trabajador:
            flash("DNI no encontrado o trabajador inactivo.", "error")
            return redirect(url_for("consumos"))

        es_adicional = 1 if request.form.get("adicional") == "1" and session.get("role") == "admin" else 0

        # REGLA FUERTE: 1 DNI = 1 consumo normal por día.
        if not es_adicional:
            duplicado = q_one("SELECT id,hora,tipo FROM consumos WHERE fecha=? AND dni=? AND COALESCE(adicional,0)=0", (fecha, dni))
            if duplicado:
                flash(f"NO DUPLICADO: el DNI {dni} ya tiene consumo registrado hoy a las {duplicado['hora']}. Solo el admin puede registrar adicional.", "error")
                return redirect(url_for("consumos"))

        tipo = request.form.get("tipo", "Almuerzo")
        if tipo not in ["Almuerzo", "Dieta"]:
            tipo = "Almuerzo"

        comedor = request.form.get("comedor", "Comedor 01")
        fundo = request.form.get("fundo", "Kawsay Allpa")
        responsable = clean_text(request.form.get("responsable"))
        cantidad = int(float(request.form.get("cantidad") or 1))
        precio = float(request.form.get("precio_unitario") or 10)
        total = cantidad * precio
        obs = clean_text(request.form.get("observacion"))

        try:
            q_exec("""
                INSERT INTO consumos(fecha,hora,dni,trabajador,empresa,area,tipo,cantidad,precio_unitario,total,observacion,comedor,fundo,responsable,adicional,estado,creado_por)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (fecha, hora_now(), dni, trabajador["nombre"], trabajador["empresa"], trabajador["area"], tipo, cantidad, precio, total, obs, comedor, fundo, responsable, es_adicional, "PENDIENTE", session["user"]))
        except Exception:
            flash(f"NO DUPLICADO: el DNI {dni} ya tiene consumo registrado para el día {fecha_peru_txt(fecha)}.", "error")
            return redirect(url_for("consumos"))

        flash("Consumo registrado correctamente." + (" Marcado como adicional." if es_adicional else ""), "ok")
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

    disabled = "disabled" if dia_cerrado(fecha) else ""
    bloqueado, msg_bloq = registro_bloqueado()
    aviso_bloq = f"<div class='flash error'>{msg_bloq}</div>" if bloqueado and session.get("role") != "admin" else ""

    filtros = filtro_bar(url_for("consumos"), fecha_inicio, fecha_fin, buscar)

    html = topbar("Registro y control de consumos", "Registra por digitación o lector QR usando el DNI") + f"""
    {aviso_bloq}

    <div class="card">
      <h3 style="margin-top:0">Registrar consumo</h3>
      <form method="post" class="form-grid">
        <input type="date" name="fecha" value="{fecha}" {disabled}>
        <input name="dni" placeholder="Digite DNI o escanee QR" required autofocus inputmode="numeric" {disabled}>
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
        <input name="responsable" placeholder="Responsable" {disabled}>
        <input type="number" name="cantidad" min="1" value="1" {disabled}>
        <input type="number" step="0.01" name="precio_unitario" value="10.00" {disabled}>
        <input name="observacion" placeholder="Observación / QR DNI" {disabled}>
        {('<label style="font-weight:900"><input type="checkbox" name="adicional" value="1"> Consumo adicional</label>' if session.get('role')=='admin' else '')}
        <button {disabled}>Registrar consumo</button>
        <a class="btn btn-blue" href="{url_for('consumos')}">Actualizar / refrescar</a>
      </form>
      <p class="muted small">Regla: no se permite duplicar DNI para el mismo día. El lector QR funciona como teclado: escanea el QR y llena el DNI.</p>
    </div>

    <br>
    {filtros}

    <div class="card">
      <div class="table-head">
        <h3>Consumos del día {fecha_peru_txt(fecha)}</h3>
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
    clave = request.form.get("clave")
    if not require_remove_key(clave):
        flash("Clave incorrecta. No se quitó el consumo.", "error")
        return redirect(request.referrer or url_for("consumos"))
    c = q_one("SELECT * FROM consumos WHERE id=?", (id_,))
    if not c:
        flash("Consumo no encontrado.", "error")
        return redirect(url_for("consumos"))
    if session.get("role") != "admin" and c["creado_por"] != session.get("user"):
        flash("Solo puedes quitar consumos registrados por tu usuario. El administrador puede quitar todos.", "error")
        return redirect(request.referrer or url_for("consumos"))
    q_exec("DELETE FROM consumos WHERE id=?", (id_,))
    flash("Consumo quitado correctamente.", "ok")
    return redirect(request.referrer or url_for("consumos"))

@app.route("/entregas", methods=["GET", "POST"])
@login_required
@roles_required("admin", "rrhh", "comedor")
def entregas():
    fecha = hoy_iso()
    dni = clean_dni(request.values.get("dni"))

    if request.method == "POST":
        if dia_cerrado(fecha):
            flash("Día cerrado. No se pueden entregar más pedidos.", "error")
            return redirect(url_for("entregas"))
        ids = request.form.getlist("ids")
        for id_ in ids:
            q_exec("UPDATE consumos SET estado='ENTREGADO', entregado_por=?, entregado_en=? WHERE id=? AND estado='PENDIENTE'",
                   (session["user"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), id_))
        flash(f"Pedidos entregados: {len(ids)}", "ok")
        return redirect(url_for("entregas", dni=dni))

    trabajador = q_one("SELECT * FROM trabajadores WHERE dni=? AND activo=1", (dni,)) if dni else None
    pedidos = q_all("SELECT * FROM consumos WHERE fecha=? AND dni=? ORDER BY hora,id", (fecha, dni)) if dni else []

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
        <input name="dni" value="{dni}" placeholder="DNI del trabajador" autofocus>
        <button class="btn-blue">Buscar</button>
      </form>
      {info}
    </div>

    <br>
    <div class="card">
      <div class="table-head">
        <h3>Pedidos del día ({fecha_peru_txt(fecha)})</h3>
        <span class="badge ok">{len(pedidos)} pedido(s)</span>
      </div>
      <form method="post">
        <input type="hidden" name="dni" value="{dni}">
        <div class="table-wrap">
          <table>
            <tr><th></th><th>#</th><th>Hora</th><th>Tipo</th><th>Cantidad</th><th>Observación</th><th>Estado</th></tr>
            {tabla}
          </table>
        </div>
        <br>
        <button>Entregar seleccionado</button>
        <button type="button" class="btn-blue" onclick="document.querySelectorAll('input[name=ids]:not(:disabled)').forEach(x=>x.checked=true)">Entregar todos</button>
      </form>
    </div>
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

        df = pd.read_excel(f, dtype=str).fillna("")
        df.columns = normalize_columns(df.columns)

        if "DNI" not in df.columns:
            flash("Falta la columna DNI. Usa la plantilla.", "error")
            return redirect(url_for("carga_masiva"))

        total = len(df)
        creados = 0
        errores = 0

        for _, r in df.iterrows():
            dni = clean_dni(r.get("DNI"))
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
@roles_required("admin", "rrhh")
def trabajadores():
    if request.method == "POST" and request.form.get("manual") == "1":
        dni = clean_dni(request.form.get("dni"))
        nombre = clean_text(request.form.get("nombre"))
        empresa = clean_text(request.form.get("empresa")) or "PRIZE"
        cargo = clean_text(request.form.get("cargo"))
        area = clean_text(request.form.get("area"))

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
        df = pd.read_excel(f, dtype=str).fillna("")
        df.columns = normalize_columns(df.columns)
        if "DNI" not in df.columns or "NOMBRE" not in df.columns:
            flash("El Excel debe tener DNI y NOMBRE.", "error")
            return redirect(url_for("trabajadores"))
        n = 0
        for _, r in df.iterrows():
            dni = clean_dni(r.get("DNI"))
            nombre = clean_text(r.get("NOMBRE"))
            if not dni or not nombre:
                continue
            empresa = clean_text(r.get("EMPRESA")) or "PRIZE"
            cargo = clean_text(r.get("CARGO"))
            area = clean_text(r.get("AREA"))
            existe = q_one("SELECT id FROM trabajadores WHERE dni=?", (dni,))
            if existe:
                q_exec("UPDATE trabajadores SET empresa=?,nombre=?,cargo=?,area=?,activo=1,actualizado=CURRENT_TIMESTAMP WHERE dni=?",
                       (empresa, nombre, cargo, area, dni))
            else:
                q_exec("INSERT INTO trabajadores(empresa,dni,nombre,cargo,area,activo) VALUES(?,?,?,?,?,1)",
                       (empresa, dni, nombre, cargo, area))
            n += 1
        flash(f"Trabajadores importados/actualizados: {n}", "ok")
        return redirect(url_for("trabajadores"))

    buscar = clean_text(request.args.get("buscar"))
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
    <div class="card">
      <h3 style="margin-top:0">Registro manual</h3>
      <form method="post" class="form-grid">
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
      <form method="post" enctype="multipart/form-data" class="form-grid">
        <input type="file" name="excel" accept=".xlsx,.xls" required>
        <button class="btn-orange">Importar trabajadores</button>
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
@roles_required("admin")
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

    usuarios = q_all("SELECT username, role, active FROM usuarios ORDER BY username")
    usuarios_html = "".join([
        f"<tr><td>{u['username']}</td><td>{u['role']}</td><td><span class='badge {'ok' if u['active'] else 'off'}'>{'Activo' if u['active'] else 'Bloqueado'}</span></td></tr>"
        for u in usuarios
    ])

    html = topbar("Configuración", "Bloqueo por horario, clave para quitar y usuarios") + f"""
    <div class="card">
      <h3 style="margin-top:0">Bloqueo de registro por horario</h3>
      <form method="post" class="form-grid">
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
        role = request.form.get("role") or "comedor"
        active = 1 if request.form.get("active") else 0
        if not username or not password:
            flash("Usuario y clave son obligatorios.", "error")
            return redirect(url_for("usuarios_admin"))

        existe = q_one("SELECT id FROM usuarios WHERE username=?", (username,))
        if existe:
            q_exec("UPDATE usuarios SET password_hash=?, role=?, active=? WHERE username=?",
                   (generate_password_hash(password), role, active, username))
            flash("Usuario actualizado.", "ok")
        else:
            q_exec("INSERT INTO usuarios(username,password_hash,role,active) VALUES(?,?,?,?)",
                   (username, generate_password_hash(password), role, active))
            flash("Usuario creado.", "ok")
        return redirect(url_for("usuarios_admin"))

    usuarios = q_all("SELECT username, role, active FROM usuarios ORDER BY username")
    tabla = "".join([
        f"<tr><td>{u['username']}</td><td>{u['role']}</td><td><span class='badge {'ok' if u['active'] else 'off'}'>{'Activo' if u['active'] else 'Bloqueado'}</span></td></tr>"
        for u in usuarios
    ])
    html = topbar("Crear usuarios y claves", "Solo administrador") + f"""
    <div class="card">
      <h3 style="margin-top:0">Crear / actualizar usuario</h3>
      <form method="post" class="form-grid">
        <input name="username" placeholder="Usuario" required>
        <input name="password" placeholder="Clave" required>
        <select name="role">
          <option value="admin">admin</option>
          <option value="rrhh">rrhh</option>
          <option value="comedor">comedor</option>
        </select>
        <label style="font-weight:900"><input type="checkbox" name="active" checked> Activo</label>
        <button>Guardar usuario</button>
      </form>
    </div>
    <br>
    <div class="card">
      <h3 style="margin-top:0">Usuarios registrados</h3>
      <div class="table-wrap"><table><tr><th>Usuario</th><th>Rol</th><th>Estado</th></tr>{tabla}</table></div>
    </div>
    """
    return render_page(html, "config")


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
    app.run(host="0.0.0.0", port=port, debug=True)
