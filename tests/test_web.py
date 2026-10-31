"""Same-origin UI assets are public; uploads and reports remain authenticated."""

import pytest


@pytest.mark.parametrize(
    "path,content_type,needle",
    [
        ("/", "text/html", 'id="upload"'),
        ("/ui/app.js", "text/javascript", "request('/auth/login'"),
        ("/ui/style.css", "text/css", "prefers-reduced-motion"),
    ],
)
async def test_ui_assets_have_safe_headers(client, path, content_type, needle):
    response = await client.get(path)
    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert needle in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


async def test_ui_does_not_expose_settings_or_private_data(client):
    for path in ["/ui/.env", "/ui/../settings.py", "/ui/settings.py", "/ui/index.html"]:
        assert (await client.get(path)).status_code == 404
    assert (await client.get("/runs/private/report")).status_code == 401
    assert (await client.post("/runs")).status_code == 401
    js = (await client.get("/ui/app.js")).text
    assert "localStorage" not in js and "sessionStorage" not in js
    assert "innerHTML" not in js
