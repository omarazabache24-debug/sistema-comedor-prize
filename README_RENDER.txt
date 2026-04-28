# Sistema Comedor PRIZE - Render

ARCHIVOS CORREGIDOS PARA RENDER:

- app.py
- requirements.txt
- Procfile
- runtime.txt

CONFIGURACION EN RENDER:

Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:app

IMPORTANTE:
No usar nombres con tilde como aplicación.py para Render.
No usar requisitos.txt como Build Command si Render está buscando requirements.txt.
