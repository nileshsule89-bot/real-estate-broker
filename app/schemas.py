from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchPropertiesInput(BaseModel):
    location: str = Field(..., min_length=2)
    budget: float = Field(..., gt=0)
    bhk: int = Field(..., ge=1, le=10)
    type: Literal["rent", "buy"]


class ShortlistPropertyInput(BaseModel):
    property_id: str


class ScheduleVisitInput(BaseModel):
    property_ids: list[str]
    preferred_time: datetime


class AssignAgentInput(BaseModel):
    visit_id: str


class RankingResult(BaseModel):
    property_id: str
    title: str
    location: str
    price: float
    bhk: int
    type: str
    score: float
