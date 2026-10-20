from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    sender_id: int
    content: str
    kind: str
    created_at: datetime | None = None


class RateIn(BaseModel):
    ratee_id: int
    score: int = Field(ge=1, le=5)
    tags: list[str] | None = None
