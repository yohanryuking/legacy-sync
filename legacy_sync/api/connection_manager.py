"""Registro simple de WebSockets conectados al dashboard en tiempo real."""

from __future__ import annotations

import asyncio

from starlette.websockets import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, message: str) -> None:
        """Envia `message` a todos los sockets conectados.

        Un socket que ya se desconecto simplemente se descarta -- el
        dashboard no necesita saberlo, el proximo fetch de resumen lo
        reconecta solo.
        """
        if not self._connections:
            return
        results = await asyncio.gather(
            *(ws.send_text(message) for ws in list(self._connections)), return_exceptions=True
        )
        for ws, result in zip(list(self._connections), results, strict=True):
            if isinstance(result, Exception):
                self._connections.discard(ws)
