import os
from io import BytesIO
from datetime import datetime, date
from functools import wraps

import pandas as pd
from flask import Flask, request, redirect, url_for, session, send_file, render_template_string, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "prize-superfruits-render-dev")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///comedor_local.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="comedor")
    active = db.Column(db.Boolean, default=True)

class Trabajador(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empresa = db.Column(db.String(120), default="PRIZE")
    dni = db.Column(db.String(20), unique=True, nullable=False)
    nombre = db.Column(db.String(180), nullable=False)
    cargo = db.Column(db.String(120), default="")
    area = db.Column(db.String(120), default="")
    activo = db.Column(db.Boolean, default=True)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

class Consumo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dni = db.Column(db.String(20), nullable=False, index=True)
    trabajador = db.Column(db.String(180), nullable=False)
    empresa = db.Column(db.String(120), default="PRIZE")
    area = db.Column(db.String(120), default="")
    fecha = db.Column(db.Date, nullable=False, default=date.today, index=True)
    tipo = db.Column(db.String(50), default="Almuerzo")
    cantidad = db.Column(db.Integer, default=1)
    precio_unitario = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)
    observacion = db.Column(db.String(250), default="")
    creado_por = db.Column(db.String(50), default="")
    creado = db.Column(db.DateTime, default=datetime.utcnow)

def seed():
    users = [("admin", "admin123", "admin"), ("rrhh", "rrhh123", "rrhh"), ("comedor", "comedor123", "comedor")]
    for u, p, r in users:
        if not User.query.filter_by(username=u).first():
            db.session.add(User(username=u, password_hash=generate_password_hash(p), role=r))
    db.session.commit()

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
            if session.get("role") not in roles and session.get("role") != "admin":
                flash("No tienes permiso para esta opción.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return wrapper
    return deco

def money(v):
    return f"S/ {float(v or 0):,.2f}"

app.jinja_env.filters["money"] = money

BASE_HTML = """
<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PRIZE ERP Comedor</title>
<style>
:root{--green:#2f8f3a;--dark:#0f172a;--blue:#2f6f95;--orange:#e66b19;--bg:#eef6f8;--card:#fff;--muted:#64748b;}
*{box-sizing:border-box} body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:linear-gradient(135deg,#eef8f3,#f8fbff);color:var(--dark)}
a{text-decoration:none;color:inherit}.layout{display:grid;grid-template-columns:260px 1fr;min-height:100vh}.side{background:#0b1f2a;color:#fff;padding:22px;position:sticky;top:0;height:100vh}.brand{text-align:center;border-bottom:1px solid rgba(255,255,255,.12);padding-bottom:18px;margin-bottom:18px}.brand img{max-width:170px;background:#fff;border-radius:18px;padding:10px}.brand h2{font-size:16px;margin:10px 0 0}.nav a{display:block;padding:12px 14px;border-radius:14px;margin:7px 0;color:#dbeafe}.nav a:hover,.nav .on{background:rgba(47,143,58,.25);color:#fff}.main{padding:26px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.pill{background:#fff;border:1px solid #d9e7eb;border-radius:999px;padding:9px 14px;color:#334155;box-shadow:0 6px 16px rgba(15,23,42,.06)}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.card{background:#fff;border:1px solid #dce7ea;border-radius:22px;padding:20px;box-shadow:0 12px 28px rgba(15,23,42,.08)}.kpi b{font-size:28px}.kpi span{display:block;color:var(--muted);font-size:13px;margin-top:5px}.tabs{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}.tab{padding:10px 14px;border-radius:12px;background:#fff;border:1px solid #dce7ea}.tab.on{background:var(--green);color:#fff}.form{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.form.full{grid-template-columns:repeat(3,1fr)}input,select,textarea{width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:12px;background:#fff}button,.btn{border:0;border-radius:12px;padding:11px 15px;background:var(--green);color:#fff;font-weight:700;cursor:pointer}.btn2{background:#2f6f95}.btn3{background:#e66b19}.table-wrap{overflow:auto;border-radius:16px;border:1px solid #dce7ea}table{width:100%;border-collapse:collapse;background:#fff}th,td{padding:11px;border-bottom:1px solid #e2e8f0;text-align:left;font-size:14px}th{background:#f1f7f5;color:#0f172a;position:sticky;top:0}.flash{padding:12px;border-radius:12px;background:#fff3cd;margin-bottom:12px}.login{max-width:430px;margin:7vh auto}.login .card{text-align:center}.login img{max-width:220px;margin-bottom:15px}.muted{color:var(--muted)}@media(max-width:950px){.layout{grid-template-columns:1fr}.side{position:relative;height:auto}.grid{grid-template-columns:1fr 1fr}.form,.form.full{grid-template-columns:1fr}.main{padding:16px}}
</style></head><body>
{% if session.get('user') %}<div class="layout"><aside class="side"><div class="brand"><img src="{{ url_for('static', filename='logo.jpeg') }}"><h2>ERP Comedor</h2><small>{{session.get('user')}} · {{session.get('role')}}</small></div><nav class="nav">
<a class="{{'on' if page=='dashboard'}}" href="{{url_for('dashboard')}}">📊 Dashboard</a>
<a class="{{'on' if page=='consumos'}}" href="{{url_for('consumos')}}">🍽️ Consumos</a>
<a class="{{'on' if page=='trabajadores'}}" href="{{url_for('trabajadores')}}">👥 Trabajadores</a>
<a class="{{'on' if page=='reportes'}}" href="{{url_for('reportes')}}">📁 Reportes Planilla</a>
<a href="{{url_for('logout')}}">🚪 Salir</a></nav></aside><main class="main">{% endif %}
{% with messages=get_flashed_messages(with_categories=true) %}{% for c,m in messages %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
{{content|safe}}
{% if session.get('user') %}</main></div>{% endif %}
</body></html>
"""

def render_page(content, page=""):
    return render_template_string(BASE_HTML, content=content, page=page)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if user and user.active and check_password_hash(user.password_hash, request.form.get("password", "")):
            session["user"] = user.username
            session["role"] = user.role
            return redirect(url_for("dashboard"))
        flash("Usuario o clave incorrecta.", "error")
    return render_page("""
    <div class='login'><div class='card'><img src='/static/logo.jpeg'><h2>Sistema Comedor PRIZE</h2><p class='muted'>Acceso ERP en Render</p>
    <form method='post'><input name='username' placeholder='Usuario' required><br><br><input name='password' type='password' placeholder='Clave' required><br><br><button style='width:100%'>Ingresar</button></form>
    <p class='muted'>admin/admin123 · rrhh/rrhh123 · comedor/comedor123</p></div></div>
    """)

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    today = date.today()
    mes = today.month; anio = today.year
    total_dia = db.session.query(db.func.sum(Consumo.total)).filter(Consumo.fecha==today).scalar() or 0
    total_mes = db.session.query(db.func.sum(Consumo.total)).filter(db.extract('month', Consumo.fecha)==mes, db.extract('year', Consumo.fecha)==anio).scalar() or 0
    cant_mes = db.session.query(db.func.sum(Consumo.cantidad)).filter(db.extract('month', Consumo.fecha)==mes, db.extract('year', Consumo.fecha)==anio).scalar() or 0
    trabajadores_activos = Trabajador.query.filter_by(activo=True).count()
    ultimos = Consumo.query.order_by(Consumo.creado.desc()).limit(10).all()
    rows = "".join([f"<tr><td>{c.fecha}</td><td>{c.dni}</td><td>{c.trabajador}</td><td>{c.tipo}</td><td>{c.cantidad}</td><td>{money(c.total)}</td></tr>" for c in ultimos])
    return render_page(f"""
    <div class='top'><div><h1>Dashboard Comedor</h1><p class='muted'>Control de consumos, planilla y trabajadores.</p></div><div class='pill'>Render + PostgreSQL</div></div>
    <div class='grid'><div class='card kpi'><b>{money(total_dia)}</b><span>Consumo de hoy</span></div><div class='card kpi'><b>{money(total_mes)}</b><span>Consumo mensual</span></div><div class='card kpi'><b>{int(cant_mes)}</b><span>Raciones del mes</span></div><div class='card kpi'><b>{trabajadores_activos}</b><span>Trabajadores activos</span></div></div><br>
    <div class='card'><h3>Últimos consumos</h3><div class='table-wrap'><table><tr><th>Fecha</th><th>DNI</th><th>Trabajador</th><th>Tipo</th><th>Cant.</th><th>Total</th></tr>{rows}</table></div></div>
    """, "dashboard")

@app.route("/consumos", methods=["GET", "POST"])
@login_required
@roles_required("admin", "rrhh", "comedor")
def consumos():
    if request.method == "POST":
        dni = request.form.get("dni", "").strip()
        t = Trabajador.query.filter_by(dni=dni).first()
        if not t or not t.activo:
            flash("DNI no encontrado o trabajador inactivo. Cárgalo primero en Trabajadores.", "error")
            return redirect(url_for("consumos"))
        cantidad = int(request.form.get("cantidad") or 1)
        precio = float(request.form.get("precio_unitario") or 0)
        fecha = datetime.strptime(request.form.get("fecha"), "%Y-%m-%d").date() if request.form.get("fecha") else date.today()
        c = Consumo(dni=dni, trabajador=t.nombre, empresa=t.empresa, area=t.area, fecha=fecha, tipo=request.form.get("tipo","Almuerzo"), cantidad=cantidad, precio_unitario=precio, total=cantidad*precio, observacion=request.form.get("observacion",""), creado_por=session.get("user",""))
        db.session.add(c); db.session.commit(); flash("Consumo registrado.", "ok"); return redirect(url_for("consumos"))
    q = Consumo.query.order_by(Consumo.fecha.desc(), Consumo.id.desc()).limit(200).all()
    rows = "".join([f"<tr><td>{c.fecha}</td><td>{c.dni}</td><td>{c.trabajador}</td><td>{c.area}</td><td>{c.tipo}</td><td>{c.cantidad}</td><td>{money(c.precio_unitario)}</td><td>{money(c.total)}</td></tr>" for c in q])
    return render_page(f"""
    <div class='top'><h1>Registro de Consumos</h1><a class='btn btn2' href='{url_for('exportar_consumos')}'>Exportar Excel</a></div>
    <div class='card'><form method='post' class='form'>
    <input name='fecha' type='date' value='{date.today()}'>
    <input name='dni' placeholder='DNI trabajador' required>
    <select name='tipo'><option>Desayuno</option><option selected>Almuerzo</option><option>Cena</option><option>Otros</option></select>
    <input name='cantidad' type='number' value='1' min='1'>
    <input name='precio_unitario' type='number' step='0.01' value='0.00' placeholder='Precio unitario'>
    <input name='observacion' placeholder='Observación'>
    <button>Registrar consumo</button></form></div><br>
    <div class='card'><h3>Últimos registros</h3><div class='table-wrap'><table><tr><th>Fecha</th><th>DNI</th><th>Trabajador</th><th>Área</th><th>Tipo</th><th>Cant.</th><th>P. Unit.</th><th>Total</th></tr>{rows}</table></div></div>
    """, "consumos")

@app.route("/trabajadores", methods=["GET", "POST"])
@login_required
@roles_required("admin", "rrhh")
def trabajadores():
    if request.method == "POST" and request.form.get("manual") == "1":
        dni = request.form.get("dni", "").strip()
        t = Trabajador.query.filter_by(dni=dni).first() or Trabajador(dni=dni)
        t.empresa=request.form.get("empresa","PRIZE"); t.nombre=request.form.get("nombre",""); t.cargo=request.form.get("cargo",""); t.area=request.form.get("area",""); t.activo=True
        db.session.add(t); db.session.commit(); flash("Trabajador guardado.", "ok"); return redirect(url_for("trabajadores"))
    if request.method == "POST" and "excel" in request.files:
        f = request.files["excel"]
        df = pd.read_excel(f)
        df.columns = [str(c).strip().upper() for c in df.columns]
        required = ["DNI", "NOMBRE"]
        if not all(c in df.columns for c in required):
            flash("El Excel debe tener mínimo columnas DNI y NOMBRE. Recomendado: EMPRESA, DNI, NOMBRE, CARGO, AREA.", "error"); return redirect(url_for("trabajadores"))
        count=0
        for _, r in df.iterrows():
            dni = str(r.get("DNI", "")).strip().split('.')[0]
            if not dni or dni.lower() == 'nan': continue
            t = Trabajador.query.filter_by(dni=dni).first() or Trabajador(dni=dni)
            t.empresa=str(r.get("EMPRESA","PRIZE") or "PRIZE").strip(); t.nombre=str(r.get("NOMBRE","")).strip(); t.cargo=str(r.get("CARGO","")).strip(); t.area=str(r.get("AREA","")).strip(); t.activo=True
            db.session.add(t); count+=1
        db.session.commit(); flash(f"Carga completada: {count} trabajadores.", "ok"); return redirect(url_for("trabajadores"))
    q = Trabajador.query.order_by(Trabajador.nombre.asc()).limit(500).all()
    rows = "".join([f"<tr><td>{t.empresa}</td><td>{t.dni}</td><td>{t.nombre}</td><td>{t.cargo}</td><td>{t.area}</td><td>{'Activo' if t.activo else 'Inactivo'}</td></tr>" for t in q])
    return render_page(f"""
    <div class='top'><h1>Trabajadores</h1><a class='btn btn2' href='{url_for('plantilla_trabajadores')}'>Descargar plantilla</a></div>
    <div class='card'><h3>Registro manual</h3><form method='post' class='form'><input type='hidden' name='manual' value='1'><input name='empresa' placeholder='Empresa' value='PRIZE'><input name='dni' placeholder='DNI' required><input name='nombre' placeholder='Nombre completo' required><input name='cargo' placeholder='Cargo'><input name='area' placeholder='Área'><button>Guardar</button></form></div><br>
    <div class='card'><h3>Carga masiva Excel</h3><form method='post' enctype='multipart/form-data'><input type='file' name='excel' accept='.xlsx,.xls' required><br><br><button class='btn3'>Importar trabajadores</button></form></div><br>
    <div class='card'><h3>Base de trabajadores</h3><div class='table-wrap'><table><tr><th>Empresa</th><th>DNI</th><th>Nombre</th><th>Cargo</th><th>Área</th><th>Estado</th></tr>{rows}</table></div></div>
    """, "trabajadores")

@app.route("/reportes")
@login_required
@roles_required("admin", "rrhh")
def reportes():
    return render_page(f"""
    <div class='top'><h1>Reportes para Planilla</h1></div>
    <div class='card'><form method='get' action='{url_for('reporte_mensual')}' class='form full'>
    <select name='mes'>{''.join([f'<option value={i} '+('selected' if i==date.today().month else '')+f'>{i:02d}</option>' for i in range(1,13)])}</select>
    <input name='anio' type='number' value='{date.today().year}'>
    <button>Descargar reporte mensual</button></form></div>
    <br><div class='card'><h3>Integración ERP</h3><p class='muted'>El Excel generado contiene DNI, trabajador, empresa, área, cantidad de consumos y total a descontar o cargar en planilla.</p></div>
    """, "reportes")

@app.route("/reporte_mensual")
@login_required
@roles_required("admin", "rrhh")
def reporte_mensual():
    mes = int(request.args.get("mes", date.today().month)); anio = int(request.args.get("anio", date.today().year))
    q = Consumo.query.filter(db.extract('month', Consumo.fecha)==mes, db.extract('year', Consumo.fecha)==anio).all()
    rows=[{"DNI":c.dni,"TRABAJADOR":c.trabajador,"EMPRESA":c.empresa,"AREA":c.area,"TIPO":c.tipo,"FECHA":c.fecha,"CANTIDAD":c.cantidad,"PRECIO_UNITARIO":c.precio_unitario,"TOTAL":c.total} for c in q]
    df = pd.DataFrame(rows)
    if not df.empty:
        resumen = df.groupby(["DNI","TRABAJADOR","EMPRESA","AREA"], as_index=False).agg(CONSUMOS=("CANTIDAD","sum"), TOTAL_PLANILLA=("TOTAL","sum"))
    else:
        resumen = pd.DataFrame(columns=["DNI","TRABAJADOR","EMPRESA","AREA","CONSUMOS","TOTAL_PLANILLA"])
    output=BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        resumen.to_excel(writer, sheet_name="RESUMEN_PLANILLA", index=False)
        df.to_excel(writer, sheet_name="DETALLE_CONSUMOS", index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"reporte_planilla_comedor_{anio}_{mes:02d}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/exportar_consumos")
@login_required
def exportar_consumos():
    q = Consumo.query.order_by(Consumo.fecha.desc()).all()
    df = pd.DataFrame([{"FECHA":c.fecha,"DNI":c.dni,"TRABAJADOR":c.trabajador,"EMPRESA":c.empresa,"AREA":c.area,"TIPO":c.tipo,"CANTIDAD":c.cantidad,"PRECIO_UNITARIO":c.precio_unitario,"TOTAL":c.total,"OBSERVACION":c.observacion} for c in q])
    output=BytesIO(); df.to_excel(output, index=False); output.seek(0)
    return send_file(output, as_attachment=True, download_name="consumos_comedor.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/plantilla_trabajadores")
@login_required
def plantilla_trabajadores():
    df = pd.DataFrame([{"EMPRESA":"PRIZE","DNI":"12345678","NOMBRE":"APELLIDOS Y NOMBRES","CARGO":"OPERARIO","AREA":"PRODUCCION"}])
    output=BytesIO(); df.to_excel(output, index=False); output.seek(0)
    return send_file(output, as_attachment=True, download_name="plantilla_trabajadores_comedor.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with app.app_context():
    db.create_all()
    seed()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
