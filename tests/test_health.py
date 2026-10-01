def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_migrations_are_idempotent(settings):
    from app import db

    conn = db.connect(settings.db_path)
    first = db.migrate(conn)
    second = db.migrate(conn)
    conn.close()
    assert first  # app lifespan has not run for this bare connection
    assert second == []
