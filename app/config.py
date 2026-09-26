from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DERIVED_DIR = DATA_DIR / "derived"
DATABASE_PATH = DATA_DIR / "avatar_factory.db"
MAX_UPLOAD_BYTES = int(os.getenv("AVATAR_FACTORY_MAX_UPLOAD_MB", "100")) * 1024 * 1024
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm"}

for directory in (DATA_DIR, UPLOAD_DIR, DERIVED_DIR):
    directory.mkdir(parents=True, exist_ok=True)
