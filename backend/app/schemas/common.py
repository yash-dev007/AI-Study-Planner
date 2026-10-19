"""Shared response envelopes and pagination helpers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Every error returned by the API has this shape."""

    error: ErrorBody


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class Page[T](BaseModel):
    items: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], *, total: int, limit: int, offset: int) -> Page[T]:
        return cls(
            items=items,
            meta=PageMeta(
                total=total, limit=limit, offset=offset, has_more=offset + len(items) < total
            ),
        )


class MessageResponse(BaseModel):
    message: str
