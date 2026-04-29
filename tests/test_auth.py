"""Tests for auth router — cookie file management.

Uses tmp_path + monkeypatch to avoid touching real data/cookies.txt.
"""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cookie_line(name: str, value: str = "v") -> bytes:
    """Build a valid Netscape cookie line (7 tab-separated fields)."""
    return f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n".encode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _filter_cookies unit tests (RED: these import _filter_cookies before it exists)
# ---------------------------------------------------------------------------

def test_filter_comments_pass_through():
    from app.routers.auth import _filter_cookies
    content = b"# Netscape HTTP Cookie File\n"
    assert _filter_cookies(content) == content


def test_filter_blank_lines_pass_through():
    from app.routers.auth import _filter_cookies
    content = b"\n\n"
    assert _filter_cookies(content) == content


def test_filter_strips_st_prefix():
    from app.routers.auth import _filter_cookies
    line = _make_cookie_line("ST-abc")
    assert _filter_cookies(line) == b""


def test_filter_strips_gps():
    from app.routers.auth import _filter_cookies
    line = _make_cookie_line("GPS")
    assert _filter_cookies(line) == b""


def test_filter_strips_secure_bucket():
    from app.routers.auth import _filter_cookies
    line = _make_cookie_line("__Secure-BUCKET")
    assert _filter_cookies(line) == b""


def test_filter_strips_secure_ynid():
    from app.routers.auth import _filter_cookies
    line = _make_cookie_line("__Secure-YNID")
    assert _filter_cookies(line) == b""


def test_filter_strips_visitor_privacy_metadata():
    from app.routers.auth import _filter_cookies
    line = _make_cookie_line("VISITOR_PRIVACY_METADATA")
    assert _filter_cookies(line) == b""


def test_filter_preserves_sid():
    from app.routers.auth import _filter_cookies
    line = _make_cookie_line("SID")
    assert _filter_cookies(line) == line


def test_filter_preserves_secure_1psid():
    from app.routers.auth import _filter_cookies
    line = _make_cookie_line("__Secure-1PSID")
    assert _filter_cookies(line) == line


def test_filter_malformed_line_pass_through():
    from app.routers.auth import _filter_cookies
    # Fewer than 7 tab-separated fields — pass through unchanged
    content = b"not\ta\tvalid\tcookie\tline\n"
    assert _filter_cookies(content) == content


def test_filter_mixed_content():
    """Comment + auth cookie + tracking cookie — only tracking stripped."""
    from app.routers.auth import _filter_cookies
    comment = b"# Netscape HTTP Cookie File\n"
    sid_line = _make_cookie_line("SID", "authvalue")
    gps_line = _make_cookie_line("GPS", "trackvalue")
    st_line = _make_cookie_line("ST-xyz", "stvalue")
    secure_psid = _make_cookie_line("__Secure-1PSID", "psidvalue")

    content = comment + sid_line + gps_line + st_line + secure_psid
    result = _filter_cookies(content)

    assert b"SID" in result
    assert b"__Secure-1PSID" in result
    assert b"GPS" not in result
    assert b"ST-xyz" not in result
    assert result == comment + sid_line + secure_psid


# ---------------------------------------------------------------------------
# Integration tests (upload endpoint)
# ---------------------------------------------------------------------------

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
    from app.routers.auth import _filter_cookies
    cookie_content = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc123\n"
    resp = await client.post("/api/auth/cookies", files={"file": ("cookies.txt", cookie_content)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    assert fake_cookies_path.exists()
    # The written content equals the filtered version of what was uploaded
    assert fake_cookies_path.read_bytes() == _filter_cookies(cookie_content)
    logger.info("Uploaded cookies to %s (%d bytes)", fake_cookies_path, len(cookie_content))


@pytest.mark.asyncio
async def test_upload_cookies_filters_tracking(client, fake_cookies_path):
    """Upload a mixed file; assert tracking cookies absent from written file."""
    sid_line = _make_cookie_line("SID", "authtoken")
    gps_line = _make_cookie_line("GPS", "gpstrack")
    st_line = _make_cookie_line("ST-123", "sttrack")
    secure_bucket = _make_cookie_line("__Secure-BUCKET", "bucket")
    mixed = b"# Netscape HTTP Cookie File\n" + sid_line + gps_line + st_line + secure_bucket

    resp = await client.post("/api/auth/cookies", files={"file": ("cookies.txt", mixed)})
    assert resp.status_code == 200

    written = fake_cookies_path.read_bytes()
    assert b"SID" in written
    assert b"GPS" not in written
    assert b"ST-123" not in written
    assert b"__Secure-BUCKET" not in written
    logger.info("Upload with filter: %d bytes in -> %d bytes out", len(mixed), len(written))


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
