from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_explicit_unimplemented_api():
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["live_analysis"] is False
        for path, method in [("/auth/login","post"), ("/runs","post"), ("/runs/test","get")]:
            response = getattr(client, method)(path)
            assert response.status_code == 501
            assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert client.get("/docs").status_code == 200
        assert client.get("/missing").status_code == 404
