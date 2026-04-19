from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.queue.worker import start_worker, stop_worker
from app.routers import auth, browse, queue, settings, summaries
from app.shutdown import request_shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await start_worker()
    yield
    request_shutdown()
    await stop_worker()


app = FastAPI(title="YouTube Summarizer", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(browse.router, prefix="/api", tags=["browse"])
app.include_router(queue.router, prefix="/api/queue", tags=["queue"])
app.include_router(summaries.router, prefix="/api/summaries", tags=["summaries"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
