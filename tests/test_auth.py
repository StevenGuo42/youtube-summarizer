"""Tests for auth router — cookie file management.

Uses tmp_path + monkeypatch to avoid touching real data/cookies.txt.
"""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

logger = logging.getLogger(__name__)


@pytest.fixture
def fake_cookies_path(tmp_path, monkeypatch):
    """Redirect COOKIES_PATH to a temp directory so real cookies stay safe."""
    fake_path = tmp_path / "cookies.txt"
    monkeypatch.setattr("app.routers.auth.COOKIES_PATH", fake_path)
    return fake_path


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_status_no_cookies(client, fake_cookies_path):
    resp = await client.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    logger.info("Status (no cookies): %s", data)
    assert data["exists"] is False
    assert data["modified"] is None


@pytest.mark.asyncio
async def test_upload_cookies(client, fake_cookies_path):
    cookie_content = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc123\n"
    resp = await client.post("/api/auth/cookies", files={"file": ("cookies.txt", cookie_content)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    assert fake_cookies_path.exists()
    assert fake_cookies_path.read_bytes() == cookie_content
    logger.info("Uploaded cookies to %s (%d bytes)", fake_cookies_path, len(cookie_content))


@pytest.mark.asyncio
async def test_status_after_upload(client, fake_cookies_path):
    fake_cookies_path.write_text("cookie data")
    resp = await client.get("/api/auth/status")
    data = resp.json()
    logger.info("Status (with cookies): %s", data)
    assert data["exists"] is True
    assert data["modified"] is not None


@pytest.mark.asyncio
async def test_delete_cookies(client, fake_cookies_path):
    fake_cookies_path.write_text("cookie data")
    assert fake_cookies_path.exists()

    resp = await client.delete("/api/auth/cookies")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert not fake_cookies_path.exists()
    logger.info("Deleted cookies successfully")


@pytest.mark.asyncio
async def test_delete_cookies_idempotent(client, fake_cookies_path):
    assert not fake_cookies_path.exists()
    resp = await client.delete("/api/auth/cookies")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    logger.info("Delete idempotent — no error when file missing")
