import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.database import get_db

router = APIRouter()


def strip_code_fence(text):
    """Strip markdown code fence wrappers (```json ... ```) from text."""
    if text and text.strip().startswith("```"):
        text = text.strip()
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
    return text


@router.get("")
async def list_summaries():
    """List all completed summaries with job info."""
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT s.id, s.job_id, s.created_at, s.structured_summary,
                   j.video_id, j.title, j.channel, j.duration, j.thumbnail_url
            FROM summaries s
            JOIN jobs j ON s.job_id = j.id
            ORDER BY s.created_at DESC
        """)
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            # Extract tldr from structured_summary for card display
            ss = d.pop("structured_summary", None)
            if ss:
                try:
                    parsed = json.loads(strip_code_fence(ss))
                    d["tldr"] = _extract_tldr(parsed)
                except json.JSONDecodeError:
                    pass
            results.append(d)
        return results
    finally:
        await db.close()


def _extract_embedded(summary_text):
    """Extract title/tldr/summary from JSON embedded in a code fence block.

    The inner JSON may have invalid escapes (e.g. \\'), so we use regex
    extraction instead of json.loads.
    """
    # Find code fence block anywhere in the text
    m = re.search(r"```(?:json)?\s*\n?\{(.*)\}\s*\n?```", summary_text, re.DOTALL)
    if not m:
        return None
    inner = "{" + m.group(1) + "}"
    # Try json.loads first (works for well-formed inner JSON)
    try:
        return json.loads(inner)
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back to regex extraction for malformed inner JSON
    result = {}
    for field in ("title", "tldr"):
        fm = re.search(rf'"{field}"\s*:\s*"([^"]*)"', inner)
        if fm:
            result[field] = fm.group(1).replace("\\n", "\n").replace('\\"', '"')
    # Extract summary field (greedy — everything until closing)
    sm = re.search(r'"summary"\s*:\s*"(.*)', inner, re.DOTALL)
    if sm:
        raw = sm.group(1)
        raw = re.sub(r'"\s*\n?\}\s*$', "", raw)
        raw = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
        result["summary"] = raw
    return result if result else None


def _extract_tldr(parsed):
    """Extract tldr from parsed structured_summary, handling embedded JSON."""
    tldr = parsed.get("tldr", "")
    if tldr and len(tldr) > 5:
        return tldr
    # If tldr is empty/placeholder, check if real content is embedded in summary
    summary = parsed.get("summary", "")
    embedded = _extract_embedded(summary)
    if embedded and embedded.get("tldr"):
        return embedded["tldr"]
    return tldr


@router.get("/{job_id}")
async def get_summary(job_id: str):
    """Get full summary for a job."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT s.*, j.video_id, j.title, j.channel, j.duration, j.thumbnail_url
               FROM summaries s
               JOIN jobs j ON s.job_id = j.id
               WHERE s.job_id = ?""",
            (job_id,),
        )
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found")

    result = dict(row)
    # Parse structured_summary JSON for the response
    if result.get("structured_summary"):
        try:
            parsed = json.loads(strip_code_fence(result["structured_summary"]))
            # Handle embedded JSON: title/tldr empty but real content in summary field
            if not parsed.get("title") or len(parsed.get("title", "")) <= 2:
                embedded = _extract_embedded(parsed.get("summary", ""))
                if embedded:
                    parsed = embedded
            result["structured"] = parsed
        except json.JSONDecodeError:
            result["structured"] = None
    return result


@router.delete("/{job_id}")
async def delete_summary(job_id: str):
    """Delete a summary and its job."""
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM summaries WHERE job_id = ?", (job_id,))
        summary_deleted = cursor.rowcount > 0
        await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await db.commit()
    finally:
        await db.close()
    if not summary_deleted:
        raise HTTPException(status_code=404, detail="Summary not found")
    return {"status": "deleted"}


@router.get("/{job_id}/export")
async def export_summary(job_id: str):
    """Export summary as markdown."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT s.structured_summary, j.title, j.channel, j.video_id
               FROM summaries s
               JOIN jobs j ON s.job_id = j.id
               WHERE s.job_id = ?""",
            (job_id,),
        )
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found")

    structured = json.loads(strip_code_fence(row["structured_summary"])) if row["structured_summary"] else {}
    # Handle embedded JSON in summary field
    if not structured.get("title") or len(structured.get("title", "")) <= 2:
        embedded = _extract_embedded(structured.get("summary", ""))
        if embedded:
            structured = embedded
    title = structured.get("title") or row["title"] or "Summary"
    tldr = structured.get("tldr", "")
    summary = structured.get("summary", "")

    md = f"# {title}\n\n"
    md += f"**Channel:** {row['channel'] or 'Unknown'}\n"
    md += f"**Video:** https://www.youtube.com/watch?v={row['video_id']}\n\n"
    if tldr:
        md += f"**TL;DR:** {tldr}\n\n"
    md += summary

    return PlainTextResponse(content=md, media_type="text/markdown")
