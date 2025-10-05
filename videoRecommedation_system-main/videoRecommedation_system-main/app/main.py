from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Tuple
import os
from dotenv import load_dotenv

from .models import Video as VideoModel, User as UserModel, Interaction as InteractionModel
from .engine import recommend, recommend_with_scores
from .storage import load_all, save_videos, save_users, save_interactions
from .config import get_settings
from .client import ExternalClient

load_dotenv()
settings = get_settings()

app = FastAPI(title="Video Recommendation API", version="0.2.0")

# In-memory state hydrated from JSON persistence
VIDEOS: Dict[str, VideoModel] = {}
USERS: Dict[str, UserModel] = {}
INTERACTIONS: List[InteractionModel] = []


class VideoCreate(BaseModel):
    id: str = Field(..., description="Unique video identifier")
    title: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    mood: Optional[str] = None


class Video(VideoCreate):
    pass


class UserCreate(BaseModel):
    id: str
    name: str
    interests: List[str] = Field(default_factory=list)
    mood: Optional[str] = None


class User(UserCreate):
    pass


class InteractionCreate(BaseModel):
    user_id: str
    video_id: str
    action: str = Field(..., description="like|view|watch|share")


@app.on_event("startup")
async def on_startup():
    data = load_all()
    global VIDEOS, USERS, INTERACTIONS
    VIDEOS = {v["id"]: VideoModel(**v) for v in data.get("videos", [])}
    USERS = {u["id"]: UserModel(**u) for u in data.get("users", [])}
    INTERACTIONS = [InteractionModel(**i) for i in data.get("interactions", [])]


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "env_loaded": bool(os.getenv("API_BASE_URL")),
        "videos": len(VIDEOS),
        "users": len(USERS),
        "interactions": len(INTERACTIONS),
    }


@app.post("/videos", response_model=Video)
async def create_video(payload: VideoCreate):
    if payload.id in VIDEOS:
        raise HTTPException(status_code=409, detail="Video already exists")
    vm = VideoModel(**payload.model_dump())
    VIDEOS[vm.id] = vm
    save_videos(list(VIDEOS.values()))
    return payload


def paginate(items: List[dict], page: int, page_size: int) -> Tuple[List[dict], int]:
    total = len(items)
    start = max((page - 1) * page_size, 0)
    end = start + page_size
    return items[start:end], total


@app.get("/videos", response_model=List[Video])
async def list_videos(
    tag: Optional[str] = Query(default=None),
    mood: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.page_size_default, ge=1, le=100),
):
    items = list(VIDEOS.values())
    if tag is not None:
        items = [v for v in items if tag in v.tags]
    if mood is not None:
        items = [v for v in items if (v.mood or "").lower() == mood.lower()]
    page_items, _ = paginate([v.model_dump() for v in items], page, page_size)
    return page_items


@app.get("/videos/{video_id}", response_model=Video)
async def get_video(video_id: str):
    video = VIDEOS.get(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@app.delete("/videos/{video_id}")
async def delete_video(video_id: str):
    if video_id not in VIDEOS:
        raise HTTPException(status_code=404, detail="Video not found")
    VIDEOS.pop(video_id)
    save_videos(list(VIDEOS.values()))
    return {"deleted": True}


@app.post("/users", response_model=User)
async def create_user(payload: UserCreate):
    if payload.id in USERS:
        raise HTTPException(status_code=409, detail="User already exists")
    um = UserModel(**payload.model_dump())
    USERS[um.id] = um
    save_users(list(USERS.values()))
    return payload


@app.get("/users/{user_id}", response_model=User)
async def get_user(user_id: str):
    user = USERS.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


class MoodUpdate(BaseModel):
    mood: Optional[str] = None


@app.patch("/users/{user_id}/mood", response_model=User)
async def update_user_mood(user_id: str, payload: MoodUpdate):
    user = USERS.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.mood = payload.mood
    save_users(list(USERS.values()))
    return user

class UserUpdate(BaseModel):
    name: Optional[str] = None
    interests: Optional[List[str]] = None
    mood: Optional[str] = None

@app.patch("/users/{user_id}", response_model=User)
async def update_user(user_id: str, payload: UserUpdate):
    user = USERS.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.name is not None:
        user.name = payload.name
    if payload.interests is not None:
        user.interests = payload.interests
    if payload.mood is not None:
        user.mood = payload.mood
    save_users(list(USERS.values()))
    return user


@app.post("/interactions")
async def record_interaction(payload: InteractionCreate):
    if payload.user_id not in USERS:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.video_id not in VIDEOS:
        raise HTTPException(status_code=404, detail="Video not found")
    im = InteractionModel(**payload.model_dump())
    INTERACTIONS.append(im)
    save_interactions(INTERACTIONS)
    return {"recorded": True}


class ScoredVideo(Video):
    score: float = Field(..., description="match percentage 0..100")


@app.get("/recommendations/{user_id}")
async def recommend_videos(
    user_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.page_size_default, ge=1, le=100),
    details: bool = Query(default=True, description="Return scores in percent if true"),
):
    user = USERS.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if details:
        ranked = recommend_with_scores(user, list(VIDEOS.values()), INTERACTIONS, limit=1000)
        # ranked is List[(pct, Video)]; paginate by video order
        vids = [v for _, v in ranked]
        page_vids, _ = paginate([v.model_dump() for v in vids], page, page_size)
        # rebuild mapping for pct
        pct_map = {v.id: pct for pct, v in ranked}
        result = []
        for vdict in page_vids[:limit]:
            result.append({**vdict, "score": round(float(pct_map.get(vdict["id"], 0.0)), 1)})
        return result
    else:
        ranked = recommend(user, list(VIDEOS.values()), INTERACTIONS, limit=1000)
        page_items, _ = paginate([v.model_dump() for v in ranked], page, page_size)
        return page_items[:limit]


@app.post("/seed/videos")
async def seed_videos():
    """Seed 10 sample videos covering multiple moods and tags."""
    samples = [
        {"id": "adv_1", "title": "Mountain Trek", "description": "Adventure in the Alps", "tags": ["adventure","travel","nature"], "mood": "adventurous"},
        {"id": "adv_2", "title": "River Rafting", "description": "Whitewater thrills", "tags": ["adventure","water"], "mood": "adventurous"},
        {"id": "rom_1", "title": "Paris Love Story", "description": "Romance in Paris", "tags": ["romance","drama"], "mood": "romance"},
        {"id": "rom_2", "title": "Sunset Date", "description": "Beach romance", "tags": ["romance","beach"], "mood": "romance"},
        {"id": "edu_1", "title": "ML Basics", "description": "Intro to Machine Learning", "tags": ["ml","education","ai"], "mood": "focused"},
        {"id": "edu_2", "title": "Algebra Refresher", "description": "Learn algebra", "tags": ["math","education"], "mood": "focused"},
        {"id": "fun_1", "title": "Comedy Skit", "description": "Laughs guaranteed", "tags": ["comedy","fun"], "mood": "cheerful"},
        {"id": "fun_2", "title": "Pranks", "description": "Harmless pranks", "tags": ["fun","viral"], "mood": "cheerful"},
        {"id": "fit_1", "title": "HIIT Workout", "description": "Quick cardio", "tags": ["fitness","health"], "mood": "energetic"},
        {"id": "calm_1", "title": "Ocean Waves", "description": "Relaxing sounds", "tags": ["relax","nature"], "mood": "calm"},
    ]
    added = 0
    for it in samples:
        vid = VideoModel(**it)
        if vid.id not in VIDEOS:
            VIDEOS[vid.id] = vid
            added += 1
    if added:
        save_videos(list(VIDEOS.values()))
    return {"seeded": added, "total": len(VIDEOS)}


@app.post("/sync/external")
async def sync_external():
    client = ExternalClient()
    items = await client.fetch_videos()
    added = 0
    for it in items:
        vid = VideoModel(
            id=str(it.get("id") or it.get("_id") or it.get("uuid")),
            title=str(it.get("title") or ""),
            description=str(it.get("description") or ""),
            tags=[str(t) for t in (it.get("tags") or [])],
            mood=(it.get("mood") or None),
        )
        if not vid.id:
            continue
        if vid.id not in VIDEOS:
            VIDEOS[vid.id] = vid
            added += 1
    if added:
        save_videos(list(VIDEOS.values()))
    return {"fetched": len(items), "added": added}


@app.get("/catalog/meta")
async def catalog_meta():
    tags = set()
    moods = set()
    for v in VIDEOS.values():
        for t in v.tags:
            tags.add(t)
        if v.mood:
            moods.add(v.mood)
    default_moods = ["adventurous","romance","focused","cheerful","energetic","calm"]
    mood_list = sorted(set(m.lower() for m in default_moods) | set((m or "").lower() for m in moods))
    tag_list = sorted(set((t or "").lower() for t in tags))
    return {"tags": tag_list, "moods": mood_list}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content="""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Moodful Recommendations</title>
  <style>
    :root {
      --bg-1: #0ea5e9; /* sky */
      --bg-2: #a78bfa; /* violet */
      --bg-3: #f472b6; /* pink */
      --card: rgba(255,255,255,0.08);
      --muted: #d1d5db;
      --text: #ffffff;
      --surface: rgba(17,24,39,0.65);
      --pill: rgba(255,255,255,0.14);
      --shadow: 0 20px 50px rgba(0,0,0,0.35);
      --radius: 18px;

      --mood-adventurous: linear-gradient(135deg, #f97316, #ef4444);
      --mood-romance: linear-gradient(135deg, #ec4899, #f43f5e);
      --mood-focused: linear-gradient(135deg, #6366f1, #22d3ee);
      --mood-cheerful: linear-gradient(135deg, #f59e0b, #f43f5e);
      --mood-energetic: linear-gradient(135deg, #22c55e, #eab308);
      --mood-calm: linear-gradient(135deg, #06b6d4, #3b82f6);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 0; color: var(--text);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, rgba(14,165,233,0.35), transparent),
                  radial-gradient(900px 600px at 110% 10%, rgba(167,139,250,0.35), transparent),
                  radial-gradient(1200px 600px at 50% 110%, rgba(244,114,182,0.28), transparent),
                  linear-gradient(135deg, #0f172a, #0b1020);
      min-height: 100vh;
      overflow-x: hidden;
    }
    .blob {
      position: fixed; filter: blur(40px); opacity: 0.55; z-index: -1; transform: translate(-50%, -50%);
      animation: float 18s ease-in-out infinite alternate;
    }
    .blob.one { width: 420px; height: 420px; top: 12%; left: 10%; background: radial-gradient(circle at 30% 30%, var(--bg-1), transparent 60%); }
    .blob.two { width: 520px; height: 520px; top: 60%; left: 85%; background: radial-gradient(circle at 70% 30%, var(--bg-2), transparent 60%); animation-duration: 22s; }
    .blob.three { width: 560px; height: 560px; top: 85%; left: 35%; background: radial-gradient(circle at 50% 50%, var(--bg-3), transparent 60%); animation-duration: 26s; }
    @keyframes float { to { transform: translate(-50%, -46%); filter: blur(52px); } }

    .container { max-width: 1040px; margin: 0 auto; padding: 34px 20px 60px; }
    .hero {
      display: grid; grid-template-columns: 1fr; gap: 14px; align-items: center; margin-bottom: 20px;
      background: linear-gradient(135deg, rgba(99,102,241,0.15), rgba(14,165,233,0.12));
      border: 1px solid rgba(255,255,255,0.14); border-radius: 22px; box-shadow: var(--shadow); padding: 24px;
    }
    @media (min-width: 860px) { .hero { grid-template-columns: 1.2fr 0.8fr; } }
    .brand { font-weight: 900; font-size: 32px; letter-spacing: 0.6px; }
    .tagline { color: var(--muted); font-size: 14px; margin-top: 4px; }
    .sparkle { font-size: 20px; margin-right: 8px; }
    .hero-art { height: 160px; border-radius: 18px; background:
      radial-gradient(400px 140px at 10% 20%, rgba(14,165,233,0.35), transparent),
      radial-gradient(340px 120px at 90% 30%, rgba(236,72,153,0.35), transparent),
      linear-gradient(135deg, #0b1020, #0f172a);
      border: 1px solid rgba(255,255,255,0.12);
    }

    .card { background: var(--card); border: 1px solid rgba(255,255,255,0.14); border-radius: var(--radius); box-shadow: var(--shadow); }
    .section { padding: 22px; margin-top: 16px; }

    .stepper { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 10px; }
    .step { padding: 10px 12px; border-radius: 999px; background: rgba(255,255,255,0.10); color: #f3f4f6; border: 1px dashed rgba(255,255,255,0.25); text-align: center; font-weight: 700; font-size: 14px; }
    .step.active { background: linear-gradient(90deg, #7c3aed, #06b6d4); color: #fff; border-style: solid; border-color: transparent; }

    .grid { display: grid; gap: 14px; }
    @media (min-width: 720px) { .grid.two { grid-template-columns: 1fr 1fr; } }
    label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
    input[type=text], select { width: 100%; background: var(--surface); color: var(--text); border: 1px solid rgba(255,255,255,0.22); border-radius: 12px; padding: 12px 14px; outline: none; transition: box-shadow .18s ease; }
    select[multiple] { height: 140px; }
    input:focus, select:focus { box-shadow: 0 0 0 3px rgba(99,102,241,0.35); }

    .btn { appearance: none; border: none; border-radius: 14px; padding: 12px 18px; font-weight: 900; cursor: pointer; background: linear-gradient(90deg, #22d3ee, #a78bfa, #f472b6); color: #0b1020; box-shadow: 0 10px 30px rgba(167,139,250,0.4); transition: transform .12s ease, filter .12s ease; }
    .btn:hover { transform: translateY(-1px); filter: brightness(1.08); }
    .btn.secondary { background: linear-gradient(90deg, #1f2937, #111827); color: #e5e7eb; border: 1px solid rgba(255,255,255,0.18); box-shadow: none; }

    .muted { color: var(--muted); font-size: 13px; }
    .hidden { display: none; }

    .recs { display: grid; gap: 14px; margin-top: 8px; }
    @media (min-width: 720px) { .recs { grid-template-columns: repeat(2, 1fr); } }
    .rec-card { padding: 16px; border-radius: 16px; border: 1px solid rgba(255,255,255,0.16); background: rgba(255,255,255,0.06); overflow: hidden; position: relative; }
    .rec-header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .rec-title { font-weight: 900; font-size: 19px; margin: 0 0 6px; }
    .badge { font-size: 12px; padding: 5px 9px; border-radius: 999px; border: 1px solid rgba(255,255,255,0.26); background: rgba(255,255,255,0.12); color: #fff; }
    .score { margin-left: auto; font-weight: 900; color: #c7f9e5; }
    .thumb { height: 120px; border-radius: 12px; margin: 8px 0; display: grid; place-items: center; color: #fff; font-size: 44px; font-weight: 900; letter-spacing: 1px; border: 1px solid rgba(255,255,255,0.16); }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip { background: var(--pill); color: #fff; padding: 6px 10px; border-radius: 999px; border: 1px solid rgba(255,255,255,0.2); font-size: 12px; }

    .thumb.mood-adventurous { background: var(--mood-adventurous); }
    .thumb.mood-romance { background: var(--mood-romance); }
    .thumb.mood-focused { background: var(--mood-focused); }
    .thumb.mood-cheerful { background: var(--mood-cheerful); }
    .thumb.mood-energetic { background: var(--mood-energetic); }
    .thumb.mood-calm { background: var(--mood-calm); }

    .toast { position: fixed; right: 16px; bottom: 16px; background: rgba(15,23,42,0.95); color: #e5e7eb; padding: 10px 14px; border: 1px solid rgba(255,255,255,0.25); border-radius: 12px; box-shadow: var(--shadow); }
  </style>
</head>
<body>
  <div class=\"blob one\"></div>
  <div class=\"blob two\"></div>
  <div class=\"blob three\"></div>

  <div class=\"container\">
    <section class=\"hero\">
      <div>
        <div class=\"brand\"><span class=\"sparkle\">✨</span>Moodful Recs</div>
        <div class=\"tagline\">Colorful, mood-aware video picks tailored to what you feel and want to listen to.</div>
        <div class=\"stepper\" style=\"margin-top:12px\">
          <div class=\"step active\" id=\"step1\">1 • Create user</div>
          <div class=\"step\" id=\"step2\">2 • Answer questions</div>
          <div class=\"step\" id=\"step3\">3 • Recommendations</div>
        </div>
      </div>
      <div class=\"hero-art\"></div>
    </section>

    <section class=\"card section\" id=\"createUserSection\">
      <div class=\"grid two\">
        <div>
          <label>Your name</label>
          <input type=\"text\" id=\"name\" placeholder=\"Enter your name\" />
        </div>
        <div>
          <label>Mood</label>
          <select id=\"mood\"></select>
        </div>
      </div>
      <div class=\"grid\" style=\"margin-top:12px\">
        <div>
          <label>Interests</label>
          <select id=\"interests\" multiple></select>
          <div class=\"muted\">Tip: Hold Ctrl or Cmd to select multiple.</div>
        </div>
      </div>
      <div style=\"margin-top:14px\">
        <button class=\"btn\" onclick=\"onCreateUser()\">Create user</button>
      </div>
    </section>

    <section class=\"card section hidden\" id=\"questionsSection\">
      <div style=\"margin-bottom:8px\" class=\"muted\" id=\"helloLbl\"></div>
      <div class=\"grid two\">
        <div>
          <label>How are you feeling now?</label>
          <select id=\"mood_now\"></select>
        </div>
        <div>
          <label>What do you want to listen to?</label>
          <select id=\"listen_tags\" multiple></select>
        </div>
      </div>
      <div class=\"grid\" style=\"margin-top:12px\">
        <div>
          <label>Anything else you like? (optional)</label>
          <input type=\"text\" id=\"extra\" placeholder=\"e.g. travel, nature, comedy\" />
        </div>
      </div>
      <div style=\"margin-top:14px\">
        <button class=\"btn\" onclick=\"onGetRecommendations()\">Get recommendations</button>
      </div>
    </section>

    <section class=\"card section hidden\" id=\"recsSection\">
      <div class=\"rec-header\" style=\"margin-bottom:10px\">
        <div class=\"muted\">Based on your mood and preferences</div>
        <button class=\"btn secondary\" style=\"margin-left:auto\" onclick=\"onRefine()\">Refine</button>
      </div>
      <div class=\"recs\" id=\"recs\"></div>
    </section>

    <div id=\"toast\" class=\"toast hidden\"></div>
  </div>

  <script>
    const base = '';
    let CURRENT_USER_ID = null;
    let CATALOG_TAGS = [];
    let CATALOG_MOODS = [];

    const MOOD_EMOJI = {
      adventurous: '🏔️', romance: '💖', focused: '🎯', cheerful: '😄', energetic: '⚡', calm: '🧘'
    };

    // Curated genre -> YouTube search links
    const GENRE_LINKS = {
      adventure: 'https://www.youtube.com/results?search_query=Skydiving+Adventure',
      calm: 'https://www.youtube.com/results?search_query=Ocean+Waves+Relaxation',
      energetic: 'https://www.youtube.com/results?search_query=Workout+Motivation+Music',
      comedy: 'https://www.youtube.com/results?search_query=Stand-up+Comedy+Russell+Peters',
      romance: 'https://www.youtube.com/results?search_query=Romantic+Short+Film',
      horror: 'https://www.youtube.com/results?search_query=Scary+Short+Film',
      'sci-fi': 'https://www.youtube.com/results?search_query=Best+Sci-Fi+Short+Film',
      action: 'https://www.youtube.com/results?search_query=Top+Gun+Action+Scene',
      drama: 'https://www.youtube.com/results?search_query=Oscar-Winning+Drama+Clip',
      mystery: 'https://www.youtube.com/results?search_query=Sherlock+Holmes+Scene',
      fantasy: 'https://www.youtube.com/results?search_query=Harry+Potter+Fan+Video',
      kids: 'https://www.youtube.com/results?search_query=Peppa+Pig+Episode',
      documentary: 'https://www.youtube.com/results?search_query=Planet+Earth+Documentary',
      travel: 'https://www.youtube.com/results?search_query=Top+10+Places+to+Visit',
      music: 'https://www.youtube.com/results?search_query=Ed+Sheeran+Shape+of+You',
      dance: 'https://www.youtube.com/results?search_query=World+of+Dance+Performance',
      cooking: 'https://www.youtube.com/results?search_query=How+to+Make+Pasta',
      sports: 'https://www.youtube.com/results?search_query=Best+Football+Goals+Compilation',
      gaming: 'https://www.youtube.com/results?search_query=Minecraft+Gameplay',
      technology: 'https://www.youtube.com/results?search_query=AI+%26+Future+of+Technology'
    };

    // Simple canonicalization for tags to match our curated keys
    function canonTag(t) {
      if (!t) return '';
      const x = String(t).toLowerCase();
      const syn = {
        'science fiction': 'sci-fi', 'sci fi': 'sci-fi', 'scifi': 'sci-fi',
        'relax': 'calm', 'calming': 'calm', 'chill': 'calm',
        'motivation': 'energetic', 'motivational': 'energetic',
        'children': 'kids', 'child': 'kids',
        'education': 'documentary', 'edu': 'documentary',
        'song': 'music',
        'food': 'cooking',
        'football': 'sports', 'soccer': 'sports',
        'game': 'gaming',
        'tech': 'technology', 'ai': 'technology',
        'crime': 'mystery',
      };
      return syn[x] || x;
    }

    function buildSearchUrl(v) {
      const fields = [v.title || '', (v.mood || ''), ...(v.tags || []).slice(0, 3)];
      const q = fields.filter(Boolean).join(' ');
      return 'https://www.youtube.com/results?search_query=' + encodeURIComponent(q);
    }

    function urlForVideo(v) {
      const tags = (v.tags || []).map(canonTag);
      const mood = canonTag(v.mood || '');
      const candidates = [...tags, mood];
      for (const c of candidates) {
        if (GENRE_LINKS[c]) return GENRE_LINKS[c];
      }
      // Try broader buckets
      if (tags.includes('adventure')) return GENRE_LINKS['adventure'];
      if (tags.includes('comedy')) return GENRE_LINKS['comedy'];
      if (tags.includes('romance')) return GENRE_LINKS['romance'];
      if (tags.includes('documentary') || tags.includes('education')) return GENRE_LINKS['documentary'];
      return buildSearchUrl(v);
    }

    function openVideoFor(v) {
      const url = urlForVideo(v);
      window.open(url, '_blank', 'noopener');
    }

    function toast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg; t.classList.remove('hidden');
      setTimeout(() => t.classList.add('hidden'), 3000);
    }
    function setStep(i) {
      for (const id of ['step1','step2','step3']) document.getElementById(id).classList.remove('active');
      document.getElementById('step'+i).classList.add('active');
    }
    function show(id) { document.getElementById(id).classList.remove('hidden'); }
    function hide(id) { document.getElementById(id).classList.add('hidden'); }

    function populateSelect(el, items, {multiple=false, placeholder='Select...'}={}) {
      el.innerHTML = '';
      if (!multiple) {
        const opt = document.createElement('option');
        opt.value = ''; opt.textContent = placeholder; el.appendChild(opt);
      }
      for (const it of items) {
        const opt = document.createElement('option');
        opt.value = it; opt.textContent = it; el.appendChild(opt);
      }
    }

    async function bootstrap() {
      try {
        const h = await fetch(base + '/health').then(r=>r.json());
        if ((h.videos||0) === 0) {
          await fetch(base + '/seed/videos', { method: 'POST' });
          toast('Seeded sample videos to get you started');
        }
      } catch (e) { /* ignore */ }
      try {
        const meta = await fetch(base + '/catalog/meta').then(r=>r.json());
        CATALOG_TAGS = meta.tags || [];
        CATALOG_MOODS = meta.moods || [];
      } catch (e) {
        CATALOG_TAGS = ['adventure','travel','nature','romance','drama','ml','education','comedy','fitness','relax'];
        CATALOG_MOODS = ['adventurous','romance','focused','cheerful','energetic','calm'];
      }
      populateSelect(document.getElementById('mood'), CATALOG_MOODS);
      populateSelect(document.getElementById('mood_now'), CATALOG_MOODS);
      const interestsSel = document.getElementById('interests');
      populateSelect(interestsSel, CATALOG_TAGS, { multiple: true }); interestsSel.multiple = true;
      const listenSel = document.getElementById('listen_tags');
      populateSelect(listenSel, CATALOG_TAGS, { multiple: true }); listenSel.multiple = true;
    }

    function genIdFromName(name) {
      const slug = (name||'user').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/(^-|-$)/g,'');
      const rnd = Math.random().toString(36).slice(2,6);
      return `${slug||'user'}-${rnd}`;
    }

    async function onCreateUser() {
      const name = document.getElementById('name').value.trim();
      const mood = document.getElementById('mood').value || null;
      const interests = Array.from(document.getElementById('interests').selectedOptions).map(o=>o.value);
      const id = genIdFromName(name);
      const body = { id, name: name || 'Guest', interests, mood };
      const r = await fetch(base + '/users', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) {
        const msg = await r.text(); toast('Could not create user: ' + msg); return;
      }
      CURRENT_USER_ID = id;
      document.getElementById('helloLbl').textContent = `Hi ${body.name}!`;
      setStep(2); hide('createUserSection'); show('questionsSection');
      confetti();
    }

    async function onGetRecommendations() {
      if (!CURRENT_USER_ID) { toast('Please create a user first'); return; }
      const moodNow = document.getElementById('mood_now').value || null;
      const listenTags = Array.from(document.getElementById('listen_tags').selectedOptions).map(o=>o.value);
      const extra = document.getElementById('extra').value.trim();
      const extraTokens = (extra ? extra.split(',') : []).map(s=>s.trim()).filter(Boolean);
      const mergedInterests = Array.from(new Set([...(listenTags||[]), ...extraTokens]));
      await fetch(`${base}/users/${encodeURIComponent(CURRENT_USER_ID)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mood: moodNow, interests: mergedInterests })
      });
      const recs = await fetch(`${base}/recommendations/${encodeURIComponent(CURRENT_USER_ID)}?limit=10&details=true`).then(r=>r.json());
      renderRecs(Array.isArray(recs) ? recs : []);
      setStep(3); hide('questionsSection'); show('recsSection');
      confetti();
    }

    function onRefine() { setStep(2); hide('recsSection'); show('questionsSection'); }

    function pill(text) { return `<span class=\"chip\">${text}</span>`; }

    function moodClass(m) { return m ? `mood-${m}` : ''; }
    function moodEmoji(m) { return MOOD_EMOJI[m] || '🎬'; }

    function renderRecs(items) {
      const wrap = document.getElementById('recs');
      wrap.innerHTML = '';
      if (!items.length) { wrap.innerHTML = '<div class=\"muted\">No recommendations yet. Try different preferences.</div>'; return; }
      for (const v of items) {
        const tags = (v.tags||[]).slice(0,6).map(pill).join(' ');
        const mood = v.mood ? `<span class=\"badge\">${v.mood}</span>` : '';
        const score = (v.score != null) ? `<div class=\"score\">${Number(v.score).toFixed(0)}%</div>` : '';
        const el = document.createElement('div');
        el.className = 'rec-card';
        const mClass = moodClass(v.mood);
        const emoji = moodEmoji(v.mood);
        el.innerHTML = `
          <div class=\"rec-header\">
            <div class=\"rec-title\">${v.title}</div>
            ${mood}
            ${score}
          </div>
          <div class=\"thumb ${mClass}\">${emoji}</div>
          <div class=\"muted\" style=\"margin:6px 0 10px\">${v.description || ''}</div>
          <div class=\"chips\">${tags}</div>
          <div style=\"margin-top:10px\"><button class=\"btn\">See this video</button></div>
        `;
        wrap.appendChild(el);
        const btn = el.querySelector('button.btn');
        btn.addEventListener('click', () => openVideoFor(v));
      }
    }

    // Tiny confetti burst
    function confetti() {
      const count = 80;
      for (let i=0;i<count;i++) {
        const s = document.createElement('span');
        s.style.position='fixed'; s.style.zIndex='9999'; s.style.pointerEvents='none';
        s.style.left=Math.random()*100+'%'; s.style.top='-10px';
        const size = 6+Math.random()*6; s.style.width=size+'px'; s.style.height=size+'px';
        s.style.borderRadius='2px';
        s.style.background = `hsl(${Math.floor(Math.random()*360)}, 90%, 60%)`;
        document.body.appendChild(s);
        const duration = 1500+Math.random()*1200;
        const translateX = (Math.random()*2-1)*120;
        s.animate([
          { transform:'translateY(0) translateX(0) rotate(0deg)', opacity:1 },
          { transform:`translateY(110vh) translateX(${translateX}px) rotate(720deg)`, opacity:0.8 }
        ], { duration, easing:'cubic-bezier(.2,.8,.2,1)' }).onfinish = ()=> s.remove();
      }
    }

    bootstrap();
  </script>
</body>
</html>
""")
