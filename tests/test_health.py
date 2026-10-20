from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_accepts_get_and_head():
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.head("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.head("/").status_code == 200