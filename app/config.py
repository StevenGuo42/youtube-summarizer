import ctypes
import os
import site
from pathlib import Path

# bitsandbytes JIT (libnvJitLink.so.13) needs cu13 NVRTC builtins. LD_LIBRARY_PATH
# can't help — the dynamic linker caches its search path at process startup, so
# in-process os.environ tweaks come too late. Force-load cu13 libs into RTLD_GLOBAL
# instead so libnvJitLink finds them when JIT triggers.
for _site_dir in site.getsitepackages():
    _cu13 = Path(_site_dir) / "nvidia" / "cu13" / "lib"
    _builtins = _cu13 / "libnvrtc-builtins.so.13.0"
    if _builtins.exists():
        ctypes.CDLL(str(_builtins), mode=ctypes.RTLD_GLOBAL)
        ctypes.CDLL(str(_cu13 / "libnvrtc.so.13"), mode=ctypes.RTLD_GLOBAL)
        break

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

# Codex backend configuration
CODEX_SCHEMA_PATH = DATA_DIR / "codex_output_schema.json"
CODEX_MAX_IMAGE_FRAMES = 50   # Hard cap per call; keep latest N by timestamp
LITELLM_MAX_IMAGE_FRAMES = 20  # Base64 payload budget; keep evenly-spaced N

# Artifact reuse: keep tmp dirs for at most this many most-recent failed/cancelled jobs
MAX_REUSABLE_FAILED_JOBS = 5
