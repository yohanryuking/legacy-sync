# Deploy (Sprint 6)

Este documento cubre la parte de Sprint 6 que **sí** se puede dejar lista
desde el repo: imagen de contenedor, orquestación local, y migraciones
versionadas. Dos cosas quedan explícitamente para quien tenga las
credenciales/cuentas del proveedor elegido — no son automatizables desde
un agente sin acceso a esas cuentas:

- **Deploy real a un proveedor gestionado** (Railway/Fly/Render/etc.).
- **Video demo** grabado a mano.

Ver el estado exacto de cada uno en [ROADMAP.md](ROADMAP.md#sprint-6--deploy-y-presentación).

## Qué existe

- `Dockerfile`: imagen única para los tres roles (API, worker, CLI). El
  comando que se le pasa al contenedor decide el rol.
- `docker-compose.prod.yml`: postgres + api + worker corriendo juntos,
  para validar el stack completo localmente antes de subirlo a un
  proveedor.
- `alembic/`: migraciones versionadas del esquema (Sprint 6) — el
  contenedor de la API corre `alembic upgrade head` antes de levantar
  `uvicorn`, así un deploy nuevo nunca sirve requests contra un esquema
  desactualizado.

## Probarlo localmente

```bash
docker compose -f docker-compose.prod.yml up --build
```

En otra terminal:

```bash
docker compose -f docker-compose.prod.yml run --rm api legacy-sync seed --count 2000
docker compose -f docker-compose.prod.yml run --rm api legacy-sync migrate --simulate-load-failures
```

Abrí http://localhost:8000 — mismo dashboard que en desarrollo, ahora
corriendo desde la imagen de contenedor. El servicio `worker` ya está
recogiendo `retry_queue` en loop (`legacy-sync worker --interval 30`) sin
que haga falta correrlo aparte.

**Nota de verificación**: la imagen se construyó (`docker build`) y se
corrió el stack completo -- Postgres, el contenedor de la API corriendo
`alembic upgrade head` seguido de `uvicorn`, y el contenedor del worker
en un proceso aparte, los tres conectados por una red Docker, exactamente
la topología que orquesta `docker-compose.prod.yml` -- durante el
desarrollo: seed, `migrate --simulate-load-failures`, y el worker
recogiendo `retry_queue` automáticamente desde su propio contenedor,
confirmado contra el dashboard vía HTTP desde el host. La primera pasada
de esta verificación dio un falso positivo por un bug real de
empaquetado (`legacy_sync/static/` no viajaba al paquete instalado con
`pip install .` sin `-e`, enmascarado por una coincidencia del `WORKDIR`
-- ver [DECISIONS.md — ADR-013](DECISIONS.md#adr-013-pip-install--e--en-el-dockerfile-no-un-build-de-wheel)
para el detalle); el `Dockerfile` ya usa `pip install -e .`, y se
re-verificó explícitamente corriendo `uvicorn` desde un `WORKDIR`
distinto de `/app` para confirmar que la resolución del paquete no
depende de esa coincidencia.

El entorno de desarrollo puntual usado para esto tiene un proxy de
salida con un certificado propio que `pip install` dentro del contenedor
no confía por defecto (nada relacionado con este proyecto); se resolvió
pasando `--network=host` al build y confiando el certificado del proxy
solo para esa verificación puntual. Nada de eso es necesario en un
entorno normal con salida a internet directa (cualquier CI, o el build
que hace el proveedor de deploy): ahí
`docker compose -f docker-compose.prod.yml up --build` funciona tal
cual, sin flags extra.

## Llevarlo a un proveedor gestionado

Los pasos son genéricos porque no se ejecutaron contra una cuenta real
(este repo no tiene credenciales de ningún proveedor). En cualquiera de
las tres opciones comunes (Railway, Fly.io, Render) el patrón es el
mismo:

1. **Postgres gestionado**: crear una instancia de Postgres 16+ en el
   proveedor. Copiar la `DATABASE_URL` que da (ajustar el driver a
   `postgresql+psycopg://` si el proveedor la da como `postgresql://`).
2. **Servicio API**: desplegar la imagen de `Dockerfile` (o dejar que el
   proveedor la construya desde el repo). Variable de entorno
   `DATABASE_URL` apuntando al Postgres del paso 1. Comando de arranque:
   `sh -c "alembic upgrade head && uvicorn legacy_sync.api.app:app --host 0.0.0.0 --port $PORT"`
   (usar `$PORT` si el proveedor lo inyecta; algunos exigen ese puerto
   específico).
3. **Servicio worker**: mismo build, mismo `DATABASE_URL`, comando
   `legacy-sync worker --interval 30`. En Railway/Fly esto es un segundo
   "service" en el mismo proyecto; en Render, un "Background Worker"
   separado del "Web Service".
4. **Variables de entorno opcionales** (ver `.env.example`):
   `SEED_RECORD_COUNT`, `SIMULATED_LOAD_FAILURE_RATE` — normalmente no
   hacen falta en producción real (son para generar/demostrar datos
   sucios), solo dejarlas si se quiere reproducir la demo en el ambiente
   desplegado.
5. Una vez arriba, correr `legacy-sync seed` y `legacy-sync migrate` como
   comandos puntuales (`railway run`, `fly ssh console`, Render Shell,
   según el proveedor) para poblar y migrar los datos de demo.

## Video demo

Guion sugerido (no grabado por el agente — para hacer a mano):

1. `legacy-sync migrate` corriendo dos veces seguidas → `legacy-sync
   status` mostrando que `migrated_customers` no creció la segunda vez.
2. Con el dashboard abierto en el navegador, correr `legacy-sync migrate
   --simulate-load-failures` en otra terminal → ver los contadores subir
   solos, sin recargar la página.
3. Un ítem en la tabla de cola de reintentos → click en "Reintentar
   ahora" → ver que pasa a `succeeded` en vivo.
4. (Opcional, más avanzado) matar `legacy-sync migrate` a mitad de camino
   con `kill -9` y mostrar `legacy-sync migrate --resume-last` retomando
   sin reprocesar lo ya confirmado.
