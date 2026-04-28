# Sistema Comedor PRIZE - listo para GitHub + Render

## Archivos incluidos
- `app.py`: aplicación Flask completa.
- `requirements.txt`: dependencias para Render.
- `runtime.txt`: versión Python.
- `Procfile`: comando para iniciar con Gunicorn.
- `static/logo.png` y `static/logo.jpeg`: logo usado por login y panel interno.
- `reportes_cierre/`: carpeta donde se guardan los cierres diarios.
- `uploads/`: carpeta para archivos temporales.

## Render
Build Command:
```bash
pip install -r requirements.txt
```
Start Command:
```bash
gunicorn app:app
```

## Variables recomendadas
- `DATABASE_URL`: PostgreSQL de Render.
- `SECRET_KEY`: clave secreta.
- `SMTP_HOST`: servidor SMTP.
- `SMTP_PORT`: 587.
- `SMTP_USER`: correo usuario.
- `SMTP_PASSWORD`: clave/app password.
- `SMTP_FROM`: correo remitente.
- `REPORTE_DESTINO`: correo por defecto para cierre.

## Usuarios iniciales
- admin / admin123
- rrhh / rrhh123
- comedor / comedor123

## Nota importante
Sube TODO el contenido del ZIP al repositorio, incluyendo la carpeta `static`.
En Render usa: **Manual Deploy -> Clear build cache & deploy** para que tome el logo nuevo.
