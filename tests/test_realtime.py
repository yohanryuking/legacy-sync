from legacy_sync.realtime import to_asyncpg_dsn


def test_to_asyncpg_dsn_strips_the_sqlalchemy_driver_suffix():
    assert (
        to_asyncpg_dsn("postgresql+psycopg://user:pass@host:5432/db")
        == "postgresql://user:pass@host:5432/db"
    )


def test_to_asyncpg_dsn_leaves_a_plain_postgres_url_untouched():
    assert to_asyncpg_dsn("postgresql://user:pass@host:5432/db") == "postgresql://user:pass@host:5432/db"
