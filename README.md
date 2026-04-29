# Sistema Comedor PRIZE - Render

## Subir a GitHub
1. Descomprime este ZIP.
2. Sube todos los archivos a tu repositorio de GitHub.
3. Conecta el repositorio en Render como Web Service.

## Configuración en Render
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`

## Usuarios demo
- admin / admin123
- rrhh / rrhh123
- comedor / comedor123

## Mejora móvil aplicada
En celular el panel lateral ya no queda fijo ocupando media pantalla. El menú se convierte en botones compactos arriba y se oculta el panel derecho para dejar más espacio a cada proceso.
