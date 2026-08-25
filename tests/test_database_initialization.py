import memory.database as database


def _disable_mongo(monkeypatch):
    monkeypatch.setattr(database, "_init_mongo_connection", lambda: None)
    monkeypatch.setattr(database, "init_mongo", lambda: None)


def test_unconfigured_postgres_is_skipped(monkeypatch):
    _disable_mongo(monkeypatch)
    monkeypatch.setitem(database.PG_CONN_INFO, "host", None)
    monkeypatch.setattr(
        database,
        "init_pg",
        lambda: (_ for _ in ()).throw(AssertionError("must not connect")),
    )

    database.init_databases()

    assert database.is_postgres_available() is False


def test_configured_postgres_is_initialized(monkeypatch):
    _disable_mongo(monkeypatch)
    monkeypatch.setitem(database.PG_CONN_INFO, "host", "localhost")
    called = []
    monkeypatch.setattr(database, "init_pg", lambda: called.append(True))

    database.init_databases()

    assert called == [True]
    assert database.is_postgres_available() is True
