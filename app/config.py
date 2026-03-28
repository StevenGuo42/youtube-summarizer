import os
from pathlib import Path

# Ensure nvm-managed Node.js is on PATH (needed by yt-dlp EJS challenge solver)
_nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
_nvm_node_bins = sorted(_nvm_dir.glob("versions/node/*/bin"), reverse=True)
if _nvm_node_bins and str(_nvm_node_bins[0]) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_nvm_node_bins[0]}:{os.environ.get('PATH', '')}"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

TMP_DIR = DATA_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "db.sqlite"
COOKIES_PATH = DATA_DIR / "cookies.txt"

VIDEO_FORMAT = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"

WHISPER_MODEL_DIR = DATA_DIR / "whisper_models"

OCR_MODEL_DIR = DATA_DIR / "ocr_models"
OCR_MODEL_NAME = "datalab-to/chandra-ocr-2"
OCR_PROMPT_TYPE = "ocr"

MAX_KEYFRAMES = 30
KEYFRAME_MAX_DIMENSION = 1024
SCENE_CHANGE_THRESHOLD = 0.3
UNIFORM_INTERVAL_SECONDS = 60
