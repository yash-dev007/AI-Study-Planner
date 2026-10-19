"""API v1 router assembly."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, courses, lectures, notifications, progress, schedule, syllabus

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(courses.router)
api_router.include_router(syllabus.router)
api_router.include_router(schedule.router)
api_router.include_router(lectures.router)
api_router.include_router(progress.router)
api_router.include_router(notifications.router)

__all__ = ["api_router"]
