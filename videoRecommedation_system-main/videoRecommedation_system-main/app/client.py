import httpx
from typing import Any, Dict, List
from .config import get_settings

class ExternalClient:
    def __init__(self):
        s = get_settings()
        self.base = s.api_base_url.rstrip('/')
        self.token = s.flic_token

    async def fetch_videos(self) -> List[Dict[str, Any]]:
        if not self.base:
            return []
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        url = f"{self.base}/videos"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get("items", [])
        except Exception:
            return []
