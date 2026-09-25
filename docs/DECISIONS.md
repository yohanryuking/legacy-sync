# Decisiones de diseño (ADRs)

Formato corto: contexto, decisión, alternativas descartadas y por qué.

## ADR-001: PostgreSQL en vez de Supabase

**Contexto**: la propuesta original de portafolio usa Supabase como destino
y como motor del dashboard en tiempo real (vía Supabase Realtime), reusando
un patrón de otro proyecto del portafolio.

**Decisión**: usar PostgreSQL "liso" (self-hosted vía Docker Compose, o
cualquier Postgres gestionado) con SQLAlchemy 2.0 como capa de acceso, y
reemplazar Supabase Realtime por `LISTEN/NOTIFY` de Postgres consumido por un
WebSocket propio en FastAPI (Fase 2).

**Por qué**:
- El valor del proyecto es demostrar ingeniería de datos (validación,
  idempotencia, resiliencia) — no integrar un BaaS. Supabase es Postgres por
  debajo; usarlo directo hace explícito lo que realmente importa (SQL,
  constraints, transacciones) en vez de esconderlo detrás de un SDK.
  Contabilizei (y cualquier empresa con sistemas legados) evalúa "sabés
  modelar datos y escribir SQL/ORM correcto", no "sabés usar el SDK de
  Supabase".
- Elimina una dependencia externa gestionada por un tercero para correr el
  proyecto localmente o en CI — todo el desarrollo y los tests corren contra
  un Postgres (o SQLite, ver ADR-003) sin cuenta ni claves de terceros.
- `LISTEN/NOTIFY` + WebSocket es un patrón igual de real en la industria que
  Supabase Realtime (que internamente también usa replication de Postgres) y
  es más transferible: funciona con cualquier Postgres, no solo con Supabase.

**Alternativas descartadas**:
- *Mantener Supabase*: descartado por lo anterior — no aporta señal técnica
  adicional para el propósito del portafolio y agrega una dependencia externa.
- *MongoDB u otra NoSQL*: descartado porque el problema es inherentemente
  relacional (una clave natural única, constraints de integridad, upserts
  transaccionales) — Postgres es el motor que mejor modela exactamente esas
  garantías, y es el que mejor encaja con un stack Python/FastAPI.

## ADR-002: Tablas separadas para datos, logs y reintentos

**Contexto**: hay tres tipos de información muy distintos generados por el
pipeline: datos ya migrados y limpios, un registro de auditoría de todo lo
que pasó, y una cola operativa de "cosas por reintentar".

**Decisión**: tres tablas (`migrated_customers`, `migration_logs`,
`retry_queue`), nunca una tabla con columnas de estado mezcladas.

**Por qué**: mezclar "datos limpios" con "registros en proceso de arreglo"
en la misma tabla obliga a filtrar por estado en cada query de negocio del
sistema nuevo, y hace fácil que un bug de filtrado exponga datos a medio
migrar. Separar por tabla hace la garantía estructural: si una fila está en
`migrated_customers`, pasó validación y carga — punto. `migration_logs` es
solo apend-only para auditoría; `retry_queue` es la única tabla operativa
"mutable" del proceso de reintento.

## ADR-003: Idempotencia vía clave natural + checksum, no solo INSERT

**Contexto**: correr una migración dos veces (por error del operador, por un
timeout que hizo reintentar el job completo) es un evento normal en
producción, no una excepción.

**Decisión**: `natural_key` (email normalizado) con unique constraint +
`checksum` del contenido, usado en `INSERT ... ON CONFLICT DO UPDATE ...
WHERE checksum changed`.

**Por qué no alcanza con "solo insertar"**: un `INSERT` simple duplica todo
en la segunda corrida. Un `INSERT ... ON CONFLICT DO NOTHING` evita
duplicados pero nunca propaga correcciones del legado (ej. un teléfono
corregido) a un registro ya migrado. La combinación clave natural + checksum
da lo mejor de ambos: no duplica, y sí actualiza cuando el contenido
realmente cambió — sin generar un `UPDATE` innecesario cuando no cambió
(relevante para no disparar triggers/`updated_at`/notificaciones de más).

**Por qué checksum y no comparar campo por campo**: comparar campo por campo
en SQL es más verboso y frágil a medida que se agregan columnas; un hash
único cubre "cambió algo" con una sola comparación, a costa de no decir
*qué* campo cambió (aceptable acá porque el dato "de verdad" siempre es el
legado, no hace falta diffear campo a campo para decidir sobrescribir).

## ADR-004: Validación con Pydantic que aísla el error, no que rompe el batch

**Contexto**: en un batch de 10.000 registros es normal que algunos tengan
datos inválidos. La forma ingenua de validar (`for row in rows:
Model(**row)`) lanza una excepción en el primer registro malo y aborta todo.

**Decisión**: `validate_legacy_record()` nunca lanza — atrapa
`pydantic.ValidationError` y la traduce a una lista estructurada de
`{field, message}`, devuelta junto con el resultado (éxito o no).

**Por qué**: el pipeline debe procesar los 9.999 registros buenos y dar
visibilidad exacta de cuál falló y por qué, en vez de fallar entero por un
registro. Esto es, literalmente, "governança/qualidade de dados" — y es lo
que hace la diferencia entre un script y un pipeline de producción.

## ADR-005: Cola de reintentos separada de la validación

**Contexto**: un registro puede fallar por dos razones muy distintas — datos
mal formados (nunca se van a poder cargar sin corregir el origen) o un fallo
transitorio de infraestructura (timeout de red, DB momentáneamente ocupada,
que sí se resuelve solo con un reintento).

**Decisión**: solo los fallos de la etapa de **carga** van a `retry_queue`.
Los fallos de **validación** solo se registran en `migration_logs` — nunca
se reintentan automáticamente, porque reintentar no cambia que el dato esté
mal formado.

**Por qué**: mezclar ambos tipos de fallo en una sola cola de reintentos
haría que el sistema reintente indefinidamente algo que nunca va a
funcionar (un email vacío no se arregla solo), desperdiciando ciclos y
ensuciando las métricas de "cuántos reintentos están realmente en curso".

## ADR-006: Backoff con jitter, y el endpoint de reintento forzado ignora el estado `failed`

**Contexto** (Sprint 3): al reintentar automáticamente ítems de
`retry_queue`, si muchos fallan al mismo tiempo (ej. un corte de red breve),
todos quedarían programados para reintentar exactamente en el mismo
instante, generando un pico de carga innecesario ("thundering herd").
Además, un ítem que agotó `max_attempts` (`status='failed'`) requiere
intervención manual — pero esa intervención necesita una forma de
dispararse.

**Decisión**: el delay de backoff (`base_seconds * 2**attempt_count`) suma
un jitter aleatorio (`0` a `retry_backoff_jitter_seconds`), y el endpoint
`POST /retry-queue/{id}/retry` reintenta cualquier ítem que no esté ya
`succeeded` — incluyendo uno `failed` — saltando tanto `next_attempt_at`
como el chequeo de `max_attempts`.

**Por qué**: el jitter dispersa los reintentos en el tiempo sin necesitar
coordinación entre ítems. Permitir forzar un ítem `failed` es lo que le da
sentido a que existan dos vías: el backoff automático es para fallos
transitorios que se resuelven solos con el tiempo, y el botón manual es
para cuando alguien ya identificó y corrigió la causa raíz (ej. arregló una
credencial vencida) y no quiere esperar a que el sistema lo detecte solo —
sin eso, un ítem `failed` quedaría huérfano para siempre salvo que se le
haga un `UPDATE` manual en la base.

## ADR-007: SQLite en tests, Postgres en producción

**Contexto**: correr Postgres en CI (o en cada sandbox de desarrollo) es una
dependencia extra que ralentiza el feedback loop de los tests.

**Decisión**: los tests (`tests/`) usan SQLite en memoria; producción y
desarrollo local usan Postgres real (vía Docker Compose).

**Por qué es seguro**: el código de negocio (`schemas.py`, `pipeline.py`,
`validate.py`) es 100% agnóstico de motor — solo `load.py` tiene una rama
explícita por dialecto (`postgresql.insert` vs `sqlite.insert`) para el
`ON CONFLICT`, ambas usando la misma API de SQLAlchemy 2.0. El comportamiento
de idempotencia se valida en ambos motores: los tests cubren la rama SQLite
automáticamente en cada corrida, y este mismo pipeline fue verificado
manualmente contra Postgres real (`python -m legacy_sync migrate` corrido
dos veces seguidas) durante el desarrollo de Fase 1.

## ADR-008: Frontend estático sin build step, en vez de un SPA de React

**Contexto** (Sprint 4): el roadmap original de portafolio proponía React
para el dashboard, reusando el patrón de otro proyecto. El dashboard de
*este* proyecto necesita mostrar unas pocas tarjetas de conteo, dos tablas,
y un botón por fila — sin routing, sin estado complejo, sin formularios.

**Decisión**: `legacy_sync/static/` es HTML + CSS + JS plano (sin
TypeScript, sin bundler, sin `node_modules`), servido directamente por la
misma app FastAPI vía `StaticFiles`.

**Por qué**: la complejidad de un SPA de React (Vite/webpack, un
`package.json` separado, un paso de build antes del deploy, hidratación,
gestión de estado) no se justifica para esta superficie de UI. Un archivo
`app.js` de ~80 líneas con `fetch` + `WebSocket` + manipulación de DOM hace
exactamente lo mismo con cero dependencias de Node y cero paso de build —
relevante porque este es un proyecto de **ingeniería de datos**, no de
frontend: el tiempo vale más invertido en el pipeline, la cola de
reintentos y el modelo de datos que en tooling de build de JS. Si el
dashboard creciera (más vistas, estado compartido entre componentes,
formularios de configuración), ahí sí se justificaría migrar a React — el
roadmap (Sprint 6) deja esa puerta abierta explícitamente, no cerrada.

**Alternativa descartada**: mantener React desde el principio. Se
descartó porque hubiera significado escribir el mismo `fetch`+`WebSocket`
dentro de `useEffect`, con el costo adicional de un bundler, sin ganar
ninguna capacidad que el dashboard actual necesite.

## ADR-009: Invalidar y refetchear, en vez de reconciliar eventos

**Contexto** (Sprint 4): cuando el WebSocket avisa que algo cambió, hay dos
formas de actualizar la UI: (a) mandar la fila completa que cambió por el
socket y que el cliente la mezcle a mano en su estado local, o (b) mandar
solo un aviso mínimo ("algo cambió en esta tabla") y que el cliente vuelva
a pedir el estado por REST.

**Decisión**: el `NOTIFY` de Postgres (y por lo tanto el mensaje de
WebSocket) lleva únicamente el nombre de la tabla afectada
(`TG_TABLE_NAME`). El frontend, al recibir cualquier mensaje, vuelve a
pedir `GET /migrations/summary` + las listas (con un debounce de 250ms
para no disparar un refetch por cada fila de un batch grande).

**Por qué**: mandar la fila completa por el trigger de Postgres
obligaría a serializar (`row_to_json`) y a que el cliente reimplemente en
JS la misma lógica de agregación que el summary ya calcula en SQL
(contar por estado, etc.) — dos lugares con la misma lógica, fácil de
desincronizar. Con "invalidar y refetchear", el SQL sigue siendo la única
fuente de verdad de los conteos, y el WebSocket es solo un disparador de
"volvé a preguntar" — más simple, y suficientemente rápido para el volumen
de este proyecto (cientos/miles de registros, no un feed de alta
frecuencia donde el costo de un refetch completo importaría).

## ADR-010: `--resume` explícito, en vez de saltear automáticamente lo ya migrado

**Contexto** (Sprint 5): al agregar checkpointing, había dos formas de
usarlo. (a) que **todo** `legacy-sync migrate` recuerde para siempre el
último `legacy_id` procesado globalmente y siempre arranque desde ahí en
adelante (más "eficiente": nunca releer nada dos veces). (b) que cada
`migrate` sin flags sea una corrida nueva e independiente que re-escanea
todo el legado desde el principio, y que retomar el punto de una corrida
interrumpida sea una acción explícita (`--resume`/`--resume-last`).

**Decisión**: la opción (b). El comportamiento de `legacy-sync migrate`
sin flags no cambió por la existencia de checkpointing.

**Por qué**: la opción (a) rompe el demo central del proyecto. Si el
checkpoint global avanzara solo, la segunda corrida de "correr la
migración dos veces y verificar que no duplica nada" (Sprint 2) dejaría de
leer y revalidar los registros ya migrados — el pipeline directamente no
los tocaría, y ya no se podría demostrar la propiedad de idempotencia del
upsert sobre esos registros en cada corrida, solo sobre los nuevos. Eso
además cambia la semántica de "correr la migración" de un sistema
legado real: un operador normalmente *quiere* poder re-correr la
migración completa cuando corrige algo en el legado (ver
[ADR-003](#adr-003-idempotencia-via-clave-natural--checksum-no-solo-insert)),
no que el sistema decida por su cuenta qué registros "ya vio" y los
ignore silenciosamente. El checkpointing resuelve un problema distinto —
"no perder todo el trabajo de una corrida específica si se cae a mitad de
camino" — y por eso retomarla es un gesto explícito (`--resume`), no un
comportamiento ambiental que cambia el significado del comando por
defecto.

## ADR-011: Migraciones de Alembic con SQL duplicado, no llamando a código compartido

**Contexto** (Sprint 6): al adoptar Alembic, la migración que instala los
triggers de `NOTIFY` (Sprint 4) podía escribirse de dos formas: (a)
importar y llamar a `legacy_sync.realtime.install_notify_triggers()`
desde la migración, reusando el mismo código que ya existía, o (b)
copiar el SQL directamente dentro del archivo de la migración.

**Decisión**: la opción (b). `alembic/versions/..._notify_triggers...py`
tiene su propia copia del SQL, y `legacy_sync/realtime.py` se redujo a lo
que sigue en uso en runtime (`NOTIFY_CHANNEL`, `to_asyncpg_dsn`) —
`install_notify_triggers()` se eliminó del módulo.

**Por qué**: una migración de Alembic es un registro histórico de "esto
es lo que se ejecutó contra la base en este punto del tiempo". Si en vez
de eso llamara a una función del código de la aplicación, esa migración
cambiaría de comportamiento silenciosamente el día que alguien edite
`realtime.py` por otro motivo — rompiendo la garantía central de
Alembic de que el historial de migraciones es reproducible y estable
para siempre, incluso contra una base vieja. Mantener la función viva en
`realtime.py` sin nada que la llamara (dead code, solo para que la
migración la importe) tampoco es mejor: confunde a quien lee el módulo
sin entender por qué existe algo que nadie invoca en runtime. Duplicar
unas pocas líneas de SQL es más barato que cualquiera de esas dos
alternativas.

## ADR-012: Contenedor único con distintos comandos, en vez de imágenes separadas por rol

**Contexto** (Sprint 6): el deploy necesita tres roles corriendo
(API, worker de reintentos, y comandos puntuales de CLI como
`migrate`/`seed`). Se podía construir una imagen de Docker por rol
(`Dockerfile.api`, `Dockerfile.worker`, ...) o una sola imagen genérica
donde el comando pasado a `docker run`/`command:` decide el rol.

**Decisión**: una sola imagen (`Dockerfile`, sin sufijo). El `CMD` por
defecto levanta la API; `docker-compose.prod.yml` sobreescribe `command:`
para el servicio de worker, y los comandos puntuales (`seed`, `migrate`)
se corren con `docker compose run --rm api legacy-sync ...`.

**Por qué**: los tres roles comparten exactamente las mismas
dependencias de Python (`legacy_sync` es un solo paquete) — no hay nada
que instalar distinto entre "la imagen que sirve la API" y "la imagen que
corre el worker". Separar en múltiples `Dockerfile`s solo agregaría
build time duplicado y el riesgo de que se desincronicen (una imagen con
una versión de una dependencia, otra con otra) sin ganar nada a cambio.
Una imagen, tres comandos, es el patrón estándar para aplicaciones
Python monolíticas con roles worker/web separados.

## ADR-013: `pip install -e .` en el Dockerfile, no un build de wheel

**Contexto** (Sprint 6): al verificar la imagen manualmente, un primer
`Dockerfile` con `RUN pip install --no-cache-dir .` (sin `-e`) **parecía**
funcionar -- la API arrancaba y el dashboard respondía. Pero era un
falso positivo: `legacy_sync/static/` no estaba declarado como
package-data en `pyproject.toml`, así que el build de wheel no lo incluía
en el paquete instalado en `site-packages`. La API igual servía los
archivos correctos por una coincidencia del entorno de verificación: el
`WORKDIR /app` del contenedor contiene una copia sin instalar de
`legacy_sync/` (copiada por el propio `Dockerfile` para el build), y en
ese Python, el directorio de trabajo termina resolviendo `import
legacy_sync` a esa copia en vez de a la de `site-packages` -- así que la
verificación estaba, sin darse cuenta, probando código fuente crudo, no
el paquete realmente instalado. Correrlo con un `WORKDIR` distinto (o
sin la copia de `legacy_sync/` accesible desde ahí) hubiera fallado al
no encontrar `static/`.

**Decisión**: `pip install -e .` (instalación editable) en vez de un
build de wheel.

**Por qué**: este proyecto es una aplicación que se despliega desde su
propio código fuente, no una librería para publicar y redistribuir a
terceros -- no hay ninguna razón para pagar el costo de un paso de
empaquetado (y sus modos de fallo, como el de `static/` de arriba) que
solo tiene sentido cuando alguien más va a `pip install legacy-sync`
desde un índice. Con instalación editable, `import legacy_sync` resuelve
siempre al mismo árbol de archivos que ya está en `/app` (copiado por el
propio `Dockerfile`), `static/` y `alembic.ini` incluidos, sin depender
de qué declare o no `pyproject.toml` como package-data, y sin que el
resultado dependa por accidente del `WORKDIR` o del directorio desde el
que se invoque el proceso. Se volvió a verificar la imagen completa
(API + worker + Postgres) con este cambio, incluyendo explícitamente
correr `uvicorn` desde un directorio de trabajo *distinto* de `/app`
(`-w /tmp`) para confirmar que la resolución del paquete ya no depende
de esa coincidencia.
