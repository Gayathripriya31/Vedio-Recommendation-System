from pydantic import BaseModel
from functools import lru_cache
import os

class Settings(BaseModel):
    api_base_url: str = os.getenv("API_BASE_URL", "")
    flic_token: str = os.getenv("FLIC_TOKEN", "")
    page_size_default: int = 10
    cache_size: int = 256

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
