import httpx

from app.main import create_app


async def test_health_and_explicit_unimplemented_api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        assert (await client.get("/health")).json()["live_analysis"] is False
        for path, method in [("/auth/login","post"), ("/runs","post"), ("/runs/test","get")]:
            response = await getattr(client, method)(path)
            assert response.status_code == 501
            assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/missing")).status_code == 404
