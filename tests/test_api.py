from app.auth import create_access_token


async def test_health_docs_and_authenticated_run_routing(client, auth_settings):
    assert (await client.get("/health")).json()["live_analysis"] is False
    headers = {"Authorization": "Bearer " + create_access_token("test-user", auth_settings)}
    for path, method in [("/runs", "post"), ("/runs/test", "get")]:
        assert (await getattr(client, method)(path)).status_code == 401
        response = await getattr(client, method)(path, headers=headers)
        assert response.status_code == (422 if path == "/runs" else 404)
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/missing")).status_code == 404
