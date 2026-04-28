# Sistema Comedor PRIZE ERP - Render

## Usuarios iniciales
- admin / admin123
- rrhh / rrhh123
- comedor / comedor123

## Deploy en Render
1. Sube esta carpeta a GitHub.
2. En Render crea `New > PostgreSQL`.
3. Copia el `DATABASE_URL` de la base.
4. Crea `New > Web Service` y conecta el repo.
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `gunicorn app:app`
7. En Environment agrega:
   - `DATABASE_URL` = URL de PostgreSQL
   - `SECRET_KEY` = cualquier clave larga, ejemplo `prize-superfruits-2026`
8. Deploy.

## Notas
- No necesitas instalar SQL Server en tu laptop.
- La app crea tablas automáticamente al iniciar.
- Puedes cargar trabajadores por Excel con columnas: EMPRESA, DNI, NOMBRE, CARGO, AREA.
- Exporta reporte mensual para planilla en Excel.
