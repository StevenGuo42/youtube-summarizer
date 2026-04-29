from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile

from app.config import COOKIES_PATH

router = APIRouter()

# Cookie names that are exact-match stripped (tracking/non-auth).
_STRIP_EXACT: frozenset[str] = frozenset({
    "GPS",
    "__Secure-BUCKET",
    "__Secure-YNID",
    "VISITOR_PRIVACY_METADATA",
})


def _filter_cookies(content: bytes) -> bytes:
    """Remove tracking/non-auth cookies from a Netscape cookie file.

    Strips any line whose cookie name (field index 5, 0-based, tab-separated)
    starts with 'ST-' or is in the exact-match strip set.  Comment lines,
    blank lines, and malformed lines (not exactly 7 fields) are preserved
    unchanged.

    Returns the filtered content re-encoded as UTF-8.
    """
    lines = content.decode("utf-8", errors="replace").splitlines(keepends=True)
    kept: list[str] = []
    for line in lines:
        stripped = line.rstrip("\r\n")
        # Preserve comments and blank lines
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        fields = stripped.split("\t")
        # Malformed lines (not 7 fields) pass through unchanged
        if len(fields) != 7:
            kept.append(line)
            continue
        name = fields[5]
        if name.startswith("ST-") or name in _STRIP_EXACT:
            continue  # strip this line
        kept.append(line)
    return "".join(kept).encode("utf-8")


@router.post("/cookies")
async def upload_cookies(file: UploadFile):
    content = await file.read()
    content = _filter_cookies(content)
    COOKIES_PATH.write_bytes(content)
    return {"status": "ok"}


@router.delete("/cookies")
async def delete_cookies():
    if COOKIES_PATH.exists():
        COOKIES_PATH.unlink()
    return {"status": "ok"}


@router.get("/status")
async def auth_status():
    if not COOKIES_PATH.exists():
        return {"exists": False, "modified": None}
    mtime = COOKIES_PATH.stat().st_mtime
    modified = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {"exists": True, "modified": modified}
