from pydantic import BaseModel, Field
from typing import Any


class OneXbetDataPayload(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)


class OneXbetDataResponse(BaseModel):
    status: str = "success"
    data: OneXbetDataPayload
