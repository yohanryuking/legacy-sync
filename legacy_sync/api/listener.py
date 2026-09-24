"""Puente entre Postgres LISTEN/NOTIFY y los WebSockets del dashboard.

Corre como una tarea de fondo del proceso de FastAPI (ver el lifespan en
`legacy_sync/api/app.py`): mantiene una conexion `asyncpg` escuchando el
canal `legacy_sync.realtime.NOTIFY_CHANNEL`, y reenvia cada aviso a todos
los clientes conectados via `ConnectionManager.broadcast`.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from legacy_sync.api.connection_manager import ConnectionManager
from legacy_sync.realtime import NOTIFY_CHANNEL

logger = logging.getLogger(__name__)


async def listen_for_notifications(dsn: str, manager: ConnectionManager) -> None:
    """Escucha `NOTIFY_CHANNEL` indefinidamente y reenvia cada aviso.

    Pensado para correr como `asyncio.Task` de fondo; se espera que quien lo
    lance lo cancele (`task.cancel()`) al apagar la app, lo que sale del
    loop via `asyncio.CancelledError` y cierra la conexion en el `finally`.

    Un fallo al conectar (Postgres caido al arrancar la app, credenciales
    mal configuradas) se loguea y termina la tarea en vez de tumbar el
    proceso de FastAPI entero -- el resto de la API sigue funcionando, solo
    que sin avisos en vivo hasta el siguiente restart.
    """
    try:
        connection = await asyncpg.connect(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        logger.error("No se pudo conectar a Postgres para LISTEN/NOTIFY: %s", exc)
        return

    def _on_notify(_connection: object, _pid: int, channel: str, payload: str) -> None:
        # El callback de asyncpg es sincrono; encolamos el broadcast (async)
        # en el loop de eventos en vez de bloquear el listener.
        asyncio.ensure_future(manager.broadcast(payload))

    await connection.add_listener(NOTIFY_CHANNEL, _on_notify)
    try:
        await asyncio.Event().wait()
    finally:
        await connection.close()
