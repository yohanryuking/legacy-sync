from legacy_sync.realtime import install_notify_triggers, to_asyncpg_dsn


def test_to_asyncpg_dsn_strips_the_sqlalchemy_driver_suffix():
    assert (
        to_asyncpg_dsn("postgresql+psycopg://user:pass@host:5432/db")
        == "postgresql://user:pass@host:5432/db"
    )


def test_to_asyncpg_dsn_leaves_a_plain_postgres_url_untouched():
    assert to_asyncpg_dsn("postgresql://user:pass@host:5432/db") == "postgresql://user:pass@host:5432/db"


def test_install_notify_triggers_is_a_noop_outside_postgres(session):
    # `session` viene de un engine SQLite -- no soporta LISTEN/NOTIFY ni
    # PL/pgSQL, asi que install_notify_triggers debe saltearse sin lanzar.
    engine = session.get_bind()
    installed = install_notify_triggers(engine)
    assert installed is False
