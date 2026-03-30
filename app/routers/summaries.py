import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.database import get_db

router = APIRouter()


@router.get("")
async def list_summaries():
    """List all completed summaries with job info."""
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT s.id, s.job_id, s.created_at,
                   j.video_id, j.title, j.channel, j.duration, j.thumbnail_url
            FROM summaries s
            JOIN jobs j ON s.job_id = j.id
            ORDER BY s.created_at DESC
        """)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


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
            result["structured"] = json.loads(result["structured_summary"])
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

    structured = json.loads(row["structured_summary"]) if row["structured_summary"] else {}
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
