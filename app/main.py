from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.routers import auth, browse, queue, summaries, settings

app = FastAPI(title="YouTube Summarizer")

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(browse.router, prefix="/api", tags=["browse"])
app.include_router(queue.router, prefix="/api/queue", tags=["queue"])
app.include_router(summaries.router, prefix="/api/summaries", tags=["summaries"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
