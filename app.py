import os
import re
import smtplib
from io import BytesIO
from datetime import datetime, date, time
from email.message import EmailMessage
from functools import wraps

import pandas as pd
from flask import Flask, request, redirect, url_for, session, send_file, render_template_string, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
REPORT_DIR = os.path.join(BASE_DIR, "reportes_cierre")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "prize-superfruits-render-dev")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or "sqlite:///comedor_local.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

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
    dni = db.Column(db.String(20), unique=True, nullable=False, index=True)
    nombre = db.Column(db.String(180), nullable=False, index=True)
    cargo = db.Column(db.String(120), default="")
    area = db.Column(db.String(120), default="", index=True)
    activo = db.Column(db.Boolean, default=True, index=True)
    actualizado = db.Column(db.DateTime, default=datetime.utcnow)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

class PedidoConsumo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False, index=True, default=date.today)
    dni = db.Column(db.String(20), nullable=False, index=True)
    trabajador = db.Column(db.String(180), default="")
    empresa = db.Column(db.String(120), default="PRIZE")
    area = db.Column(db.String(120), default="")
    tipo = db.Column(db.String(50), default="Almuerzo")
    cantidad = db.Column(db.Integer, default=1)
    precio_unitario = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)
    observacion = db.Column(db.String(250), default="")
    estado = db.Column(db.String(20), default="PENDIENTE", index=True)
    creado_por = db.Column(db.String(50), default="")
    entregado_por = db.Column(db.String(50), default="")
    entregado_en = db.Column(db.DateTime, nullable=True)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

class CierreDia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, unique=True, nullable=False, index=True)
    cerrado_por = db.Column(db.String(50), default="")
    cerrado_en = db.Column(db.DateTime, default=datetime.utcnow)
    total_pedidos = db.Column(db.Integer, default=0)
    total_entregados = db.Column(db.Integer, default=0)
    total_pendientes = db.Column(db.Integer, default=0)
    total_importe = db.Column(db.Float, default=0.0)
    archivo_excel = db.Column(db.String(255), default="")
    correo_destino = db.Column(db.String(200), default="")
    correo_estado = db.Column(db.String(80), default="")

# ---------- helpers ----------
def clean_text(v):
    if pd.isna(v): return ""
    return str(v).strip()

def clean_dni(v):
    if pd.isna(v): return ""
    s = re.sub(r"\D", "", str(v).strip().replace(".0", ""))
    return s.zfill(8) if 1 <= len(s) < 8 else s

def normalize_columns(cols):
    out=[]
    for c in cols:
        x=str(c).strip().upper()
        for a,b in [("Á","A"),("É","E"),("Í","I"),("Ó","O"),("Ú","U"),("Ñ","N")]: x=x.replace(a,b)
        out.append(re.sub(r"\s+", "_", x))
    return out

def money(v): return f"S/ {float(v or 0):,.2f}"
app.jinja_env.filters["money"] = money

def today_closed(fecha=None):
    return CierreDia.query.filter_by(fecha=fecha or date.today()).first()

def send_report_email(to_email, subject, body, attachment_path):
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_FROM", user or "no-reply@prize.local")
    if not (host and user and password and to_email):
        note = os.path.join(REPORT_DIR, f"correo_no_enviado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        with open(note, "w", encoding="utf-8") as f:
            f.write("SMTP no configurado. El Excel sí fue generado.\n\n")
            f.write(f"Para: {to_email}\nAsunto: {subject}\nAdjunto: {attachment_path}\n\n{body}")
        return "NO ENVIADO - SMTP NO CONFIGURADO"
    msg = EmailMessage()
    msg["From"] = sender; msg["To"] = to_email; msg["Subject"] = subject
    msg.set_content(body)
    with open(attachment_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=os.path.basename(attachment_path))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls(); smtp.login(user, password); smtp.send_message(msg)
    return "ENVIADO"

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user"): return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if session.get("role") != "admin" and session.get("role") not in roles:
                flash("No tienes permiso para esta opción.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return wrapper
    return deco

BASE_HTML = r"""
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sistema Comedor PRIZE</title><style>
:root{--green:#169b45;--green2:#0f7a35;--dark:#061b27;--blue:#0b6fad;--orange:#ff6b13;--bg:#f4f8fb;--line:#e4edf2;--muted:#64748b;--ok:#16a34a;--danger:#dc2626}*{box-sizing:border-box}body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:linear-gradient(135deg,#f7fbff,#f2fff7);color:#102033}a{text-decoration:none;color:inherit}.hero{margin:10px;border:1px solid var(--line);border-radius:14px;background:white;display:grid;grid-template-columns:340px 1fr 300px;gap:22px;align-items:center;padding:18px;box-shadow:0 8px 24px #0f172a12}.hero img{max-width:245px;max-height:120px;object-fit:contain}.hero h1{font-size:34px;margin:0 0 7px}.checks{columns:2;font-weight:650;color:#23364a}.checks div{margin:6px 0}.users{background:#061b27;color:white;border-radius:12px;padding:18px;line-height:1.9}.layout{display:grid;grid-template-columns:250px 1fr;min-height:calc(100vh - 175px)}.side{background:linear-gradient(180deg,#09283a,#061722);color:white;padding:18px;margin-left:10px;border-radius:16px 16px 0 0}.brand{text-align:center;border-bottom:1px solid #ffffff22;padding-bottom:15px}.brand img{width:100px;height:70px;object-fit:contain;background:#fff;border-radius:12px}.nav a{display:block;padding:12px 14px;border-radius:12px;margin:6px 0;color:#dbeafe;font-weight:700}.nav a:hover,.nav .on{background:linear-gradient(90deg,#169b45,#0b6fad);color:white}.main{padding:0 10px 20px 22px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 14px}.top h2{font-size:28px;margin:0}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 10px 24px #0f172a10}.kpi b{font-size:28px;color:#102033}.kpi span{display:block;color:var(--muted);font-size:13px}.form{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}input,select,textarea{width:100%;padding:12px;border:1px solid #cfdae6;border-radius:12px;background:white}button,.btn{border:0;border-radius:12px;padding:12px 16px;background:var(--green);color:white;font-weight:800;cursor:pointer;display:inline-block}.btn2{background:var(--blue)}.btn3{background:var(--orange)}.btn-danger{background:var(--danger)}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px}table{width:100%;border-collapse:collapse;background:white}th,td{padding:11px;border-bottom:1px solid #edf2f7;text-align:left;white-space:nowrap;font-size:13px}th{background:#f4faf7}.badge{border-radius:999px;padding:5px 10px;font-weight:800;font-size:12px;display:inline-block}.ok{background:#dcfce7;color:#166534}.off{background:#fee2e2;color:#991b1b}.warn{background:#fef3c7;color:#92400e}.flash{margin-bottom:12px;padding:12px 14px;border-radius:12px;background:#eff6ff;border:1px solid #bfdbfe}.flash.error{background:#fef2f2;border-color:#fecaca;color:#991b1b}.flash.ok{background:#f0fdf4;border-color:#bbf7d0;color:#166534}.login{max-width:460px;margin:40px auto}.login .card{text-align:center}.login img{max-width:260px}.actions{display:flex;gap:8px;flex-wrap:wrap}.notice{border-left:5px solid var(--green);padding:11px;background:#f0fdf4;border-radius:12px;margin:10px 0}.filters{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:10px;margin-bottom:12px}.footer{background:#061b27;color:#dbeafe;padding:18px 28px;margin-top:10px;display:flex;justify-content:space-between}.mini{font-size:12px;color:#64748b}@media(max-width:1050px){.hero{grid-template-columns:1fr}.checks{columns:1}.layout{grid-template-columns:1fr}.side{margin-right:10px}.grid{grid-template-columns:1fr 1fr}.form,.filters{grid-template-columns:1fr}.main{padding:12px}.top{display:block}}
</style></head><body>
{% if session.get('user') %}<div class="hero"><div><img src="/static/logo.png?v=2"></div><div><h1>Sistema Comedor PRIZE</h1><p class="muted">ERP para la Gestión del Comedor Corporativo</p><div class="checks"><div>✅ Registro y control de consumos</div><div>✅ Entrega de pedidos validando DNI</div><div>✅ Carga masiva de consumos Excel</div><div>✅ Cierre de día y reportes</div><div>✅ Envío automático por correo</div><div>✅ Roles y permisos</div></div></div><div class="users"><b>Usuarios de prueba</b><br>👤 admin / admin123<br>👥 rrhh / rrhh123<br>🍽️ comedor / comedor123</div></div><div class="layout"><aside class="side"><div class="brand"><img src="{{url_for('static',filename='logo.png')}}"><h3>ERP Comedor</h3><small>{{session.get('user')}} · {{session.get('role')}}</small></div><nav class="nav"><a class="{{'on' if page=='dashboard'}}" href="{{url_for('dashboard')}}">📊 Dashboard</a><a class="{{'on' if page=='pedidos'}}" href="{{url_for('pedidos')}}">🍽️ Consumos</a><a class="{{'on' if page=='entregas'}}" href="{{url_for('entregas')}}">✅ Entregas</a><a class="{{'on' if page=='carga'}}" href="{{url_for('carga_masiva')}}">📥 Carga Masiva</a><a class="{{'on' if page=='trabajadores'}}" href="{{url_for('trabajadores')}}">👥 Trabajadores</a><a class="{{'on' if page=='cierre'}}" href="{{url_for('cierre_dia')}}">🔒 Cierre de Día</a><a href="{{url_for('logout')}}">🚪 Salir</a></nav></aside><main class="main">{% endif %}{% with messages=get_flashed_messages(with_categories=true) %}{% for c,m in messages %}<div class="flash {{c}}">{{m}}</div>{% endfor %}{% endwith %}{{content|safe}}{% if session.get('user') %}</main></div><div class="footer"><span>© 2026 Prize Superfruits - Comedor Corporativo</span><span>Versión 2.0.0</span></div>{% endif %}</body></html>
"""

def render_page(content, page=""):
    return render_template_string(BASE_HTML, content=content, page=page)

def seed():
    for username, password, role in [("admin","admin123","admin"),("rrhh","rrhh123","rrhh"),("comedor","comedor123","comedor")]:
        if not User.query.filter_by(username=username).first():
            db.session.add(User(username=username,password_hash=generate_password_hash(password),role=role))
    db.session.commit()

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=User.query.filter_by(username=request.form.get('username','').strip()).first()
        if u and u.active and check_password_hash(u.password_hash, request.form.get('password','')):
            session['user']=u.username; session['role']=u.role; return redirect(url_for('dashboard'))
        flash('Usuario o clave incorrecta.', 'error')
    return render_page("""<div class='login'><div class='card'><img src='/static/logo.png'><h2>Sistema Comedor PRIZE</h2><p class='muted'>Acceso al sistema</p><form method='post'><input name='username' placeholder='Usuario' required><br><br><input name='password' type='password' placeholder='Clave' required><br><br><button style='width:100%'>Ingresar</button></form><p class='mini'>admin/admin123 · rrhh/rrhh123 · comedor/comedor123</p></div></div>""")
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    hoy=date.today(); cerrado=today_closed(hoy)
    total=db.session.query(db.func.sum(PedidoConsumo.total)).filter(PedidoConsumo.fecha==hoy).scalar() or 0
    pedidos=PedidoConsumo.query.filter_by(fecha=hoy).count(); entregados=PedidoConsumo.query.filter_by(fecha=hoy,estado='ENTREGADO').count(); pend=PedidoConsumo.query.filter_by(fecha=hoy,estado='PENDIENTE').count()
    workers=Trabajador.query.filter_by(activo=True).count(); ult=PedidoConsumo.query.filter_by(fecha=hoy).order_by(PedidoConsumo.creado.desc()).limit(10).all()
    rows=''.join([f"<tr><td>{c.creado.strftime('%H:%M')}</td><td>{c.dni}</td><td>{c.trabajador}</td><td>{c.area}</td><td>{c.tipo}</td><td>{c.cantidad}</td><td>{money(c.total)}</td><td><span class='badge {'ok' if c.estado=='ENTREGADO' else 'warn'}'>{c.estado}</span></td></tr>" for c in ult]) or "<tr><td colspan=8>Sin pedidos de hoy.</td></tr>"
    estado = "DÍA CERRADO" if cerrado else "DÍA ABIERTO"
    btn = "" if cerrado else f"<a class='btn btn3' href='{url_for('cierre_dia')}'>Cerrar día y consolidar</a>"
    return render_page(f"""<div class='top'><div><h2>Dashboard</h2><p class='muted'>Resumen general del sistema</p></div><div><span class='badge {'off' if cerrado else 'ok'}'>{estado}</span></div></div><div class='grid'><div class='card kpi'><b>{pedidos}</b><span>Pedidos de hoy</span></div><div class='card kpi'><b>{entregados}</b><span>Entregados</span></div><div class='card kpi'><b>{workers}</b><span>Trabajadores activos</span></div><div class='card kpi'><b>{money(total)}</b><span>Total del día</span></div></div><br><div class='grid' style='grid-template-columns:3fr 1fr'><div class='card'><h3>Consumos de hoy</h3><div class='table-wrap'><table><tr><th>Hora</th><th>DNI</th><th>Trabajador</th><th>Área</th><th>Tipo</th><th>Cant.</th><th>Total</th><th>Estado</th></tr>{rows}</table></div></div><div class='card'><h3>Estado del día</h3><p><span class='badge {'off' if cerrado else 'ok'}'>{estado}</span></p><p class='muted'>Fecha: {hoy.strftime('%d/%m/%Y')}</p><p>Pendientes: <b>{pend}</b></p>{btn}</div></div>""", 'dashboard')

@app.route('/pedidos', methods=['GET','POST'])
@login_required
@roles_required('admin','rrhh','comedor')
def pedidos():
    if request.method=='POST':
        fecha=datetime.strptime(request.form.get('fecha') or str(date.today()), '%Y-%m-%d').date()
        if today_closed(fecha): flash('El día ya está cerrado. No se puede registrar más pedidos.', 'error'); return redirect(url_for('pedidos'))
        dni=clean_dni(request.form.get('dni','')); t=Trabajador.query.filter_by(dni=dni).first()
        if not t or not t.activo: flash('DNI no existe o trabajador inactivo en la base.', 'error'); return redirect(url_for('pedidos'))
        cant=int(request.form.get('cantidad') or 1); precio=float(request.form.get('precio_unitario') or 0)
        p=PedidoConsumo(fecha=fecha,dni=dni,trabajador=t.nombre,empresa=t.empresa,area=t.area,tipo=request.form.get('tipo','Almuerzo'),cantidad=cant,precio_unitario=precio,total=cant*precio,observacion=request.form.get('observacion',''),creado_por=session.get('user',''))
        db.session.add(p); db.session.commit(); flash('Pedido/consumo registrado y queda pendiente para entrega.', 'ok'); return redirect(url_for('pedidos'))
    hoy=date.today(); data=PedidoConsumo.query.filter_by(fecha=hoy).order_by(PedidoConsumo.creado.desc()).limit(300).all()
    rows=''.join([f"<tr><td>{c.creado.strftime('%H:%M')}</td><td>{c.dni}</td><td>{c.trabajador}</td><td>{c.area}</td><td>{c.tipo}</td><td>{c.cantidad}</td><td>{money(c.total)}</td><td><span class='badge {'ok' if c.estado=='ENTREGADO' else 'warn'}'>{c.estado}</span></td></tr>" for c in data]) or '<tr><td colspan=8>Sin registros.</td></tr>'
    disabled='disabled' if today_closed(hoy) else ''
    return render_page(f"""<div class='top'><h2>Registro y control de consumos</h2><a class='btn btn2' href='{url_for('exportar_pedidos')}'>Exportar Excel</a></div><div class='card'><h3>Registrar pedido manual</h3><form method='post' class='form'><input type='date' name='fecha' value='{hoy}' {disabled}><input name='dni' placeholder='Digite DNI' required {disabled}><select name='tipo' {disabled}><option>Almuerzo</option><option>Desayuno</option><option>Cena</option><option>Otros</option></select><input type='number' name='cantidad' value='1' min='1' {disabled}><input type='number' step='0.01' name='precio_unitario' value='10.00' placeholder='Precio' {disabled}><input name='observacion' placeholder='Observación' {disabled}><button {disabled}>Registrar</button></form></div><br><div class='card'><h3>Pedidos del día</h3><div class='table-wrap'><table><tr><th>Hora</th><th>DNI</th><th>Trabajador</th><th>Área</th><th>Tipo</th><th>Cant.</th><th>Total</th><th>Estado</th></tr>{rows}</table></div></div>""", 'pedidos')

@app.route('/entregas', methods=['GET','POST'])
@login_required
@roles_required('admin','rrhh','comedor')
def entregas():
    dni=clean_dni(request.values.get('dni','')); hoy=date.today()
    if request.method=='POST' and request.form.get('accion'):
        ids=[int(x) for x in request.form.getlist('ids')]
        if today_closed(hoy): flash('Día cerrado. No se pueden entregar más pedidos.', 'error'); return redirect(url_for('entregas'))
        q=PedidoConsumo.query.filter(PedidoConsumo.id.in_(ids), PedidoConsumo.estado=='PENDIENTE').all()
        for p in q: p.estado='ENTREGADO'; p.entregado_por=session.get('user',''); p.entregado_en=datetime.utcnow()
        db.session.commit(); flash(f'Pedidos entregados: {len(q)}', 'ok'); return redirect(url_for('entregas', dni=dni))
    t=Trabajador.query.filter_by(dni=dni).first() if dni else None
    pedidos=[]
    if dni: pedidos=PedidoConsumo.query.filter_by(fecha=hoy,dni=dni).order_by(PedidoConsumo.creado).all()
    info = f"<div class='notice'><b>{t.nombre}</b> · {t.area} · <span class='badge ok'>Activo</span></div>" if t and t.activo else ("<div class='flash error'>DNI no encontrado o inactivo.</div>" if dni else "")
    rows=''.join([f"<tr><td><input type='checkbox' name='ids' value='{p.id}' {'disabled' if p.estado!='PENDIENTE' else 'checked'}></td><td>{p.creado.strftime('%H:%M')}</td><td>{p.tipo}</td><td>{p.cantidad}</td><td>{money(p.total)}</td><td><span class='badge {'ok' if p.estado=='ENTREGADO' else 'warn'}'>{p.estado}</span></td></tr>" for p in pedidos]) or '<tr><td colspan=6>Sin pedidos para este DNI hoy.</td></tr>'
    return render_page(f"""<div class='top'><h2>Entrega de pedidos validando DNI</h2></div><div class='card'><form method='get' class='form' style='grid-template-columns:2fr auto'><input name='dni' value='{dni}' placeholder='Digite DNI del trabajador' autofocus><button class='btn2'>Buscar</button></form>{info}</div><br><div class='card'><h3>Pedidos del día ({hoy.strftime('%d/%m/%Y')})</h3><form method='post'><input type='hidden' name='dni' value='{dni}'><input type='hidden' name='accion' value='entregar'><div class='table-wrap'><table><tr><th></th><th>Hora</th><th>Tipo</th><th>Cantidad</th><th>Total</th><th>Estado</th></tr>{rows}</table></div><br><button>Entregar seleccionado</button> <button class='btn2' onclick="document.querySelectorAll('input[name=ids]:not(:disabled)').forEach(x=>x.checked=true)">Entregar todos</button></form></div>""", 'entregas')

@app.route('/carga_masiva', methods=['GET','POST'])
@login_required
@roles_required('admin','rrhh','comedor')
def carga_masiva():
    if request.method=='POST':
        if today_closed(date.today()): flash('Día cerrado. No se permite cargar más consumos para hoy.', 'error'); return redirect(url_for('carga_masiva'))
        f=request.files.get('excel')
        if not f or not f.filename.lower().endswith(('.xlsx','.xls')): flash('Sube un Excel válido.', 'error'); return redirect(url_for('carga_masiva'))
        df=pd.read_excel(f,dtype=str).fillna(''); df.columns=normalize_columns(df.columns)
        if 'DNI' not in df.columns: flash('Falta columna DNI. Columnas sugeridas: FECHA, DNI, TIPO, CANTIDAD, PRECIO_UNITARIO, OBSERVACION', 'error'); return redirect(url_for('carga_masiva'))
        creados=actualizados=errores=0
        for _,r in df.iterrows():
            dni=clean_dni(r.get('DNI','')); t=Trabajador.query.filter_by(dni=dni).first()
            if not t or not t.activo: errores+=1; continue
            fecha=pd.to_datetime(r.get('FECHA',''), errors='coerce').date() if clean_text(r.get('FECHA','')) else date.today()
            if today_closed(fecha): errores+=1; continue
            tipo=clean_text(r.get('TIPO','Almuerzo')) or 'Almuerzo'; cant=int(float(r.get('CANTIDAD',1) or 1)); precio=float(r.get('PRECIO_UNITARIO', r.get('PRECIO',10)) or 10)
            # Evita duplicados exactos del Forms para el mismo día/DNI/tipo
            exists=PedidoConsumo.query.filter_by(fecha=fecha,dni=dni,tipo=tipo,estado='PENDIENTE').first()
            if exists: actualizados+=1; exists.cantidad=cant; exists.precio_unitario=precio; exists.total=cant*precio; exists.observacion=clean_text(r.get('OBSERVACION',''))
            else:
                db.session.add(PedidoConsumo(fecha=fecha,dni=dni,trabajador=t.nombre,empresa=t.empresa,area=t.area,tipo=tipo,cantidad=cant,precio_unitario=precio,total=cant*precio,observacion=clean_text(r.get('OBSERVACION','')),creado_por=session.get('user',''))); creados+=1
        db.session.commit(); flash(f'Carga masiva terminada: {creados} creados, {actualizados} actualizados, {errores} con error/no encontrados.', 'ok' if errores==0 else 'error'); return redirect(url_for('carga_masiva'))
    hist=PedidoConsumo.query.order_by(PedidoConsumo.creado.desc()).limit(12).all()
    rows=''.join([f"<tr><td>{p.fecha}</td><td>{p.dni}</td><td>{p.trabajador}</td><td>{p.tipo}</td><td>{p.estado}</td></tr>" for p in hist]) or '<tr><td colspan=5>Sin historial.</td></tr>'
    return render_page(f"""<div class='top'><h2>Carga masiva de consumos</h2><a class='btn btn2' href='{url_for('plantilla_consumos')}'>Descargar plantilla Excel</a></div><div class='card'><div class='notice'>Sirve para importar los pedidos del Google Forms. Columnas: <b>FECHA, DNI, TIPO, CANTIDAD, PRECIO_UNITARIO, OBSERVACION</b>.</div><form method='post' enctype='multipart/form-data'><input type='file' name='excel' accept='.xlsx,.xls' required><br><br><button class='btn3'>Importar consumos</button></form></div><br><div class='card'><h3>Últimos importados/registrados</h3><div class='table-wrap'><table><tr><th>Fecha</th><th>DNI</th><th>Trabajador</th><th>Tipo</th><th>Estado</th></tr>{rows}</table></div></div>""", 'carga')

@app.route('/cierre_dia', methods=['GET','POST'])
@login_required
@roles_required('admin')
def cierre_dia():
    hoy=date.today(); cerrado=today_closed(hoy)
    if request.method=='POST':
        if cerrado: flash('Este día ya fue cerrado.', 'error'); return redirect(url_for('cierre_dia'))
        to_email=request.form.get('correo','').strip()
        pedidos=PedidoConsumo.query.filter_by(fecha=hoy).order_by(PedidoConsumo.area, PedidoConsumo.trabajador).all()
        df=pd.DataFrame([{'FECHA':p.fecha,'DNI':p.dni,'TRABAJADOR':p.trabajador,'EMPRESA':p.empresa,'AREA':p.area,'TIPO':p.tipo,'CANTIDAD':p.cantidad,'PRECIO_UNITARIO':p.precio_unitario,'TOTAL':p.total,'ESTADO':p.estado,'CREADO_POR':p.creado_por,'ENTREGADO_POR':p.entregado_por,'OBSERVACION':p.observacion} for p in pedidos])
        resumen = df.groupby(['AREA','ESTADO'], as_index=False).agg(CANTIDAD=('CANTIDAD','sum'), TOTAL=('TOTAL','sum')) if not df.empty else pd.DataFrame(columns=['AREA','ESTADO','CANTIDAD','TOTAL'])
        usuarios = df.groupby(['CREADO_POR'], as_index=False).agg(PEDIDOS=('DNI','count'), TOTAL=('TOTAL','sum')) if not df.empty else pd.DataFrame(columns=['CREADO_POR','PEDIDOS','TOTAL'])
        filename=f"cierre_comedor_{hoy.strftime('%Y_%m_%d')}.xlsx"; path=os.path.join(REPORT_DIR, filename)
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='DETALLE_DIA', index=False); resumen.to_excel(writer, sheet_name='RESUMEN_AREA', index=False); usuarios.to_excel(writer, sheet_name='RESUMEN_USUARIOS', index=False)
        total_pedidos=len(pedidos); entregados=sum(1 for p in pedidos if p.estado=='ENTREGADO'); pendientes=total_pedidos-entregados; importe=sum(p.total for p in pedidos)
        estado_correo=send_report_email(to_email, f"Cierre comedor PRIZE {hoy.strftime('%d/%m/%Y')}", f"Se adjunta cierre del día. Pedidos: {total_pedidos}. Entregados: {entregados}. Pendientes: {pendientes}. Total: {money(importe)}", path)
        db.session.add(CierreDia(fecha=hoy,cerrado_por=session.get('user',''),total_pedidos=total_pedidos,total_entregados=entregados,total_pendientes=pendientes,total_importe=importe,archivo_excel=filename,correo_destino=to_email,correo_estado=estado_correo)); db.session.commit()
        flash(f'Día cerrado. Reporte generado en carpeta reportes_cierre: {filename}. Correo: {estado_correo}', 'ok'); return redirect(url_for('cierre_dia'))
    pedidos=PedidoConsumo.query.filter_by(fecha=hoy).count(); entregados=PedidoConsumo.query.filter_by(fecha=hoy,estado='ENTREGADO').count(); pendientes=pedidos-entregados; total=db.session.query(db.func.sum(PedidoConsumo.total)).filter_by(fecha=hoy).scalar() or 0
    status = f"<span class='badge off'>DÍA CERRADO</span><p>Archivo: <b>{cerrado.archivo_excel}</b></p><a class='btn btn2' href='{url_for('descargar_reporte_cierre', filename=cerrado.archivo_excel)}'>Descargar reporte</a>" if cerrado else "<span class='badge ok'>DÍA ABIERTO</span>"
    form = "" if cerrado else f"<form method='post'><input name='correo' placeholder='Correo destino' value='{os.getenv('REPORTE_DESTINO','administracion@prize.pe')}'><br><br><button class='btn3'>Cerrar día y enviar reporte</button></form>"
    return render_page(f"""<div class='top'><h2>Cierre de Día y Reportes</h2></div><div class='grid'><div class='card kpi'><b>{pedidos}</b><span>Total pedidos</span></div><div class='card kpi'><b>{entregados}</b><span>Entregados</span></div><div class='card kpi'><b>{pendientes}</b><span>Pendientes</span></div><div class='card kpi'><b>{money(total)}</b><span>Total facturado</span></div></div><br><div class='card'><h3>Estado del día</h3>{status}<div class='notice'>Al cerrar el día, se consolida el reporte de todos los usuarios, se guarda el Excel en la carpeta <b>reportes_cierre</b> junto al app.py y se intenta enviar por correo.</div>{form}</div>""", 'cierre')

@app.route('/trabajadores', methods=['GET','POST'])
@login_required
@roles_required('admin','rrhh')
def trabajadores():
    if request.method=='POST' and request.form.get('manual')=='1':
        dni=clean_dni(request.form.get('dni','')); t=Trabajador.query.filter_by(dni=dni).first() or Trabajador(dni=dni)
        t.empresa=clean_text(request.form.get('empresa','PRIZE')) or 'PRIZE'; t.nombre=clean_text(request.form.get('nombre','')); t.cargo=clean_text(request.form.get('cargo','')); t.area=clean_text(request.form.get('area','')); t.activo=True; db.session.add(t); db.session.commit(); flash('Trabajador guardado.', 'ok'); return redirect(url_for('trabajadores'))
    if request.method=='POST' and 'excel' in request.files:
        f=request.files.get('excel'); df=pd.read_excel(f,dtype=str).fillna(''); df.columns=normalize_columns(df.columns)
        if 'DNI' not in df.columns or 'NOMBRE' not in df.columns: flash('Faltan DNI y NOMBRE.', 'error'); return redirect(url_for('trabajadores'))
        n=0
        for _,r in df.iterrows():
            dni=clean_dni(r.get('DNI','')); nombre=clean_text(r.get('NOMBRE',''))
            if not dni or not nombre: continue
            t=Trabajador.query.filter_by(dni=dni).first() or Trabajador(dni=dni)
            t.empresa=clean_text(r.get('EMPRESA','PRIZE')) or 'PRIZE'; t.nombre=nombre; t.cargo=clean_text(r.get('CARGO','')); t.area=clean_text(r.get('AREA','')); t.activo=True; db.session.add(t); n+=1
        db.session.commit(); flash(f'Trabajadores importados/actualizados: {n}', 'ok'); return redirect(url_for('trabajadores'))
    q=Trabajador.query.order_by(Trabajador.nombre).limit(800).all(); rows=''.join([f"<tr><td>{t.empresa}</td><td>{t.dni}</td><td>{t.nombre}</td><td>{t.cargo}</td><td>{t.area}</td><td><span class='badge ok'>Activo</span></td></tr>" for t in q]) or '<tr><td colspan=6>Sin trabajadores.</td></tr>'
    return render_page(f"""<div class='top'><h2>Trabajadores</h2><a class='btn btn2' href='{url_for('plantilla_trabajadores')}'>Descargar plantilla</a></div><div class='card'><h3>Registro manual</h3><form method='post' class='form'><input type='hidden' name='manual' value='1'><input name='empresa' placeholder='Empresa' value='PRIZE'><input name='dni' placeholder='DNI' required><input name='nombre' placeholder='Nombre completo' required><input name='cargo' placeholder='Cargo'><input name='area' placeholder='Área'><button>Guardar</button></form></div><br><div class='card'><h3>Carga masiva</h3><form method='post' enctype='multipart/form-data'><input type='file' name='excel' accept='.xlsx,.xls' required><br><br><button class='btn3'>Importar trabajadores</button></form></div><br><div class='card'><h3>Base de trabajadores</h3><div class='table-wrap'><table><tr><th>Empresa</th><th>DNI</th><th>Nombre</th><th>Cargo</th><th>Área</th><th>Estado</th></tr>{rows}</table></div></div>""", 'trabajadores')

@app.route('/plantilla_consumos')
@login_required
def plantilla_consumos():
    df=pd.DataFrame([{'FECHA':date.today(),'DNI':'12345678','TIPO':'Almuerzo','CANTIDAD':1,'PRECIO_UNITARIO':10,'OBSERVACION':'Pedido desde Forms'}]); output=BytesIO(); df.to_excel(output,index=False); output.seek(0); return send_file(output,as_attachment=True,download_name='plantilla_carga_consumos_forms.xlsx',mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
@app.route('/plantilla_trabajadores')
@login_required
def plantilla_trabajadores():
    df=pd.DataFrame([{'EMPRESA':'PRIZE','DNI':'12345678','NOMBRE':'APELLIDOS Y NOMBRES','CARGO':'OPERARIO','AREA':'PRODUCCION'}]); output=BytesIO(); df.to_excel(output,index=False); output.seek(0); return send_file(output,as_attachment=True,download_name='plantilla_trabajadores.xlsx',mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
@app.route('/exportar_pedidos')
@login_required
def exportar_pedidos():
    q=PedidoConsumo.query.order_by(PedidoConsumo.fecha.desc(), PedidoConsumo.id.desc()).all(); df=pd.DataFrame([{'FECHA':p.fecha,'DNI':p.dni,'TRABAJADOR':p.trabajador,'AREA':p.area,'TIPO':p.tipo,'CANTIDAD':p.cantidad,'TOTAL':p.total,'ESTADO':p.estado} for p in q]); output=BytesIO(); df.to_excel(output,index=False); output.seek(0); return send_file(output,as_attachment=True,download_name='pedidos_consumos_comedor.xlsx',mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
@app.route('/descargar_cierre/<path:filename>')
@login_required
def descargar_reporte_cierre(filename):
    return send_file(os.path.join(REPORT_DIR, os.path.basename(filename)), as_attachment=True)

with app.app_context():
    db.create_all(); seed()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT',5000)), debug=True)
