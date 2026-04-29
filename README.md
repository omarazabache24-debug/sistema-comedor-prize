# Sistema Comedor PRIZE - Final PRO Render

## Mejoras incluidas
- Botón **Actualizar / refrescar** en Entregas corregido: ahora llama al API y recarga pedidos sin salir de la pantalla.
- Si no ingresas DNI en Entregas, el refresco muestra todos los pedidos del día.
- Usuarios guardados en SQLite con `commit` real y claves protegidas con hash.
- Auditoría de creación/actualización de usuarios.
- Persistencia local en `comedor_prize.db` para usuarios, consumos, entregas, reportes y cierres.
- Interfaz reforzada para celular desde login y módulos internos.
- Listo para GitHub + Render.

## Usuarios demo
- adm1 / adm1
- adm2 / adm2
- admin / admin123
- comedor / comedor123

## Render
Build command:
```bash
pip install -r requirements.txt
```
Start command:
```bash
gunicorn app:app
```

## Correo de auditoría de usuarios
Por seguridad, el sistema **no envía contraseñas por correo**. Sí puede enviar una notificación segura con usuario, rol, acción y fecha.

Variables opcionales en Render:
- ENABLE_ADMIN_USER_ALERTS=1
- ADMIN_AUDIT_EMAIL=omar.azabache24@gmail.com
- SMTP_HOST=smtp.gmail.com
- SMTP_PORT=587
- SMTP_USER=tu_correo@gmail.com
- SMTP_PASSWORD=tu_password_app
- SMTP_FROM=tu_correo@gmail.com

Si no configuras SMTP, el sistema genera el registro en `reportes_cierre/notificaciones_usuarios.txt`.
