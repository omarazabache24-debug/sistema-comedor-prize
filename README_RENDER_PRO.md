# Sistema Comedor PRIZE - Render PRO

Correcciones incluidas:
- Se corrigió el loop de redirecciones `ERR_TOO_MANY_REDIRECTS`.
- Se corrigió el decorador de roles para que `comedor` y `rrhh` puedan entrar a `/consumos`.
- Se agregó configuración estable de sesión/cookies para Render.
- Se agregó `ProxyFix` para trabajar correctamente detrás del proxy HTTPS de Render.
- Se mantiene Procfile para despliegue con Gunicorn.

## Variables recomendadas en Render
En Render > Environment agrega:

SECRET_KEY=prize-super-seguro-2026-omar
SESSION_COOKIE_SECURE=1
SESSION_COOKIE_SAMESITE=Lax

## Login demo
- admin1 / admin123  -> administrador total
- admin2 / admin123  -> administrador total
- admin / admin123   -> usuario comedor
- rrhh / rrhh123     -> usuario comedor
- comedor / comedor123 -> usuario comedor

## Importante
Después de subir este ZIP a GitHub y hacer Deploy, borra las cookies antiguas del dominio onrender.com si el navegador todavía muestra error anterior.
