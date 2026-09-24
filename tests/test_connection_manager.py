import asyncio

from legacy_sync.api.connection_manager import ConnectionManager


class _FakeWebSocket:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.accepted = False
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self.fail:
            raise RuntimeError("socket cerrado")
        self.sent.append(message)


def test_broadcast_reaches_every_connected_socket():
    async def scenario():
        manager = ConnectionManager()
        ws_a, ws_b = _FakeWebSocket(), _FakeWebSocket()
        await manager.connect(ws_a)
        await manager.connect(ws_b)

        await manager.broadcast("migrated_customers")

        assert ws_a.sent == ["migrated_customers"]
        assert ws_b.sent == ["migrated_customers"]

    asyncio.run(scenario())


def test_broadcast_drops_sockets_that_fail_to_send_without_raising():
    async def scenario():
        manager = ConnectionManager()
        healthy = _FakeWebSocket()
        broken = _FakeWebSocket(fail=True)
        await manager.connect(healthy)
        await manager.connect(broken)

        await manager.broadcast("retry_queue")  # no debe lanzar por `broken`

        assert healthy.sent == ["retry_queue"]
        # El socket roto se descarta: un segundo broadcast no vuelve a tocarlo.
        await manager.broadcast("retry_queue")
        assert healthy.sent == ["retry_queue", "retry_queue"]

    asyncio.run(scenario())


def test_disconnect_removes_the_socket():
    async def scenario():
        manager = ConnectionManager()
        ws = _FakeWebSocket()
        await manager.connect(ws)
        manager.disconnect(ws)

        await manager.broadcast("migration_logs")

        assert ws.sent == []

    asyncio.run(scenario())
