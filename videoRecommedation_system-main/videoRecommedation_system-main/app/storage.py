import json
from pathlib import Path
from typing import Dict, List
from .models import Video, User, Interaction

DATA_DIR = Path("data")
VIDEOS_FILE = DATA_DIR / "videos.json"
USERS_FILE = DATA_DIR / "users.json"
INTERACTIONS_FILE = DATA_DIR / "interactions.json"

for p in (VIDEOS_FILE, USERS_FILE, INTERACTIONS_FILE):
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[]", encoding="utf-8")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_all() -> Dict[str, List[dict]]:
    return {
        "videos": load_json(VIDEOS_FILE),
        "users": load_json(USERS_FILE),
        "interactions": load_json(INTERACTIONS_FILE),
    }


def save_videos(videos: List[Video]):
    save_json(VIDEOS_FILE, [v.model_dump() for v in videos])


def save_users(users: List[User]):
    save_json(USERS_FILE, [u.model_dump() for u in users])


def save_interactions(interactions: List[Interaction]):
    save_json(INTERACTIONS_FILE, [i.model_dump() for i in interactions])
