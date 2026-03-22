from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

TMP_DIR = DATA_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "db.sqlite"
COOKIES_PATH = DATA_DIR / "cookies.txt"

VIDEO_FORMAT = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"

WHISPER_MODEL_DIR = DATA_DIR / "whisper_models"

MAX_KEYFRAMES = 30
KEYFRAME_MAX_DIMENSION = 1024
SCENE_CHANGE_THRESHOLD = 0.3
UNIFORM_INTERVAL_SECONDS = 60
