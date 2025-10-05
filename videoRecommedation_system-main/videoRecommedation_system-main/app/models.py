from pydantic import BaseModel, Field
from typing import List, Optional

class Video(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    mood: Optional[str] = None

class User(BaseModel):
    id: str
    name: str
    interests: List[str] = Field(default_factory=list)
    mood: Optional[str] = None

class Interaction(BaseModel):
    user_id: str
    video_id: str
    action: str
