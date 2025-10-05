from typing import Dict, List, Tuple
from collections import defaultdict
from functools import lru_cache
from .models import Video, User, Interaction
from .config import get_settings


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in (text or "").replace("/", " ").replace("-", " ").split()]


def content_score(user: User, video: Video) -> float:
    weights: Dict[str, float] = defaultdict(float)
    for tag in user.interests:
        weights[tag.lower()] += 1.0
    for token in tokenize(user.name):
        weights[token] += 0.2
    score = 0.0
    for tag in video.tags:
        score += weights.get(tag.lower(), 0.0)
    for token in tokenize(video.title) + tokenize(video.description or ""):
        score += 0.1 * weights.get(token, 0.0)
    if user.mood and video.mood and user.mood.lower() == video.mood.lower():
        score += 0.5
    return score


def graph_score(user_id: str, interactions: List[Interaction], videos_by_id: Dict[str, Video]) -> Dict[str, float]:
    # Simple co-occurrence: videos interacted by same user boost related tags
    tag_boost: Dict[str, float] = defaultdict(float)
    for inter in interactions:
        if inter.user_id == user_id:
            vid = videos_by_id.get(inter.video_id)
            if vid:
                for tag in vid.tags:
                    tag_boost[tag.lower()] += 0.5
    video_score: Dict[str, float] = defaultdict(float)
    for vid in videos_by_id.values():
        for tag in vid.tags:
            video_score[vid.id] += tag_boost.get(tag.lower(), 0.0)
    return video_score


def mood_fallback(user: User, candidates: List[Video]) -> List[Video]:
    if not user.mood:
        return candidates
    preferred = [v for v in candidates if v.mood and v.mood.lower() == user.mood.lower()]
    return preferred or candidates


@lru_cache(maxsize=get_settings().cache_size)
def _empty_cache_marker(_: str) -> int:  # helper for cache namespace
    return 0


def recommend(user: User, videos: List[Video], interactions: List[Interaction], limit: int = 10) -> List[Video]:
    videos_by_id = {v.id: v for v in videos}
    gscore = graph_score(user.id, interactions, videos_by_id)
    scored: List[Tuple[float, Video]] = []
    for v in videos:
        c = content_score(user, v)
        g = gscore.get(v.id, 0.0)
        scored.append((c + g, v))
    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [v for s, v in scored]
    ranked = mood_fallback(user, ranked)
    return ranked[:limit]


def recommend_with_scores(user: User, videos: List[Video], interactions: List[Interaction], limit: int = 10) -> List[Tuple[float, Video]]:
    """Return (score, video) list with scores normalized to 0..100 per request.
    The raw score is content_score + graph_score; normalization is by max score among candidates.
    """
    videos_by_id = {v.id: v for v in videos}
    gscore = graph_score(user.id, interactions, videos_by_id)
    raw: List[Tuple[float, Video]] = []
    for v in videos:
        c = content_score(user, v)
        g = gscore.get(v.id, 0.0)
        raw.append((c + g, v))
    # Sort by raw score
    raw.sort(key=lambda x: x[0], reverse=True)
    # Mood fallback order
    raw_ranked = [(s, v) for (s, v) in raw]
    ranked_videos = [v for _, v in raw_ranked]
    ranked_videos = mood_fallback(user, ranked_videos)
    # Rebuild in that order keeping scores
    ordered = []
    score_map = {v.id: s for (s, v) in raw_ranked}
    for v in ranked_videos:
        ordered.append((score_map.get(v.id, 0.0), v))
    # Normalize scores to 0..100
    max_score = max((s for s, _ in ordered), default=1.0)
    normalized = []
    for s, v in ordered[:limit]:
        pct = 0.0 if max_score <= 0 else (s / max_score) * 100.0
        normalized.append((pct, v))
    return normalized
