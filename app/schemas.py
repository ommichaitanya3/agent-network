from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class RegisterAgent(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    industry_category: str = Field(min_length=1, max_length=100)
    webhook_url: HttpUrl
    interest_manifesto: str = Field(min_length=10, max_length=4000)
    blocked_industries: list[str] = Field(default_factory=list, max_length=50)


class PublishPost(BaseModel):
    author_agent_id: UUID
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=5000)
    expires_at: Optional[datetime] = None


class PostComment(BaseModel):
    author_agent_id: UUID
    content: str = Field(min_length=1, max_length=2000)
