"""Generic primitives reused across CA's API surface."""

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class CAPaginationMeta(BaseModel):
    """Inner meta object on initial-fetch responses; absent on pagination follow-ups."""

    page_number: int
    page_size: int
    total_count: int


class CAPagination(BaseModel):
    """CA's pagination envelope. Same shape across every collection in CA's API."""

    self: Optional[str] = None
    first: Optional[str] = None
    prev: Optional[str] = None
    next: Optional[str] = None
    total_count: Optional[int] = None
    meta: Optional[CAPaginationMeta] = None


class CAPaginatedCollection(BaseModel, Generic[T]):
    """Generic { values: [...], pagination: {...} } envelope."""

    values: list[T] = Field(default_factory=list)
    pagination: Optional[CAPagination] = None


class CADateOfBirth(BaseModel):
    """CA's structured DOB. All fields nullable on output."""

    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    date: Optional[str] = None
