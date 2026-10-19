"""Development bootstrap: demo data only, never in production.

The seed creates one professor account and one example course with a real
syllabus structure, weekly teaching windows, and a holiday, so the API can be
explored immediately. It is idempotent and disabled outside development.
"""

from __future__ import annotations

import logging
from datetime import date, time, timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import session_scope
from app.models.entities import (
    AvailabilityRule,
    CalendarException,
    Course,
    CourseStatus,
    DependencyType,
    ExceptionType,
    SyllabusUnit,
    Topic,
    TopicDependency,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)

DEMO_UNITS: list[tuple[str, str, list[tuple[str, int, str, list[str]]]]] = [
    (
        "Unit 1: Foundations",
        "Complexity analysis, linear structures, and asymptotic reasoning.",
        [
            ("Algorithm analysis and asymptotic notation", 90, "medium", []),
            ("Arrays, strings, and dynamic arrays", 60, "easy", []),
            ("Linked lists and pointer discipline", 90, "medium", []),
        ],
    ),
    (
        "Unit 2: Trees",
        "Hierarchical structures and their traversal strategies.",
        [
            ("Binary search trees", 90, "medium", []),
            ("Tree traversals and recursion patterns", 60, "easy", []),
            ("Balanced trees: AVL and red-black overview", 120, "hard", []),
        ],
    ),
    (
        "Unit 3: Graphs",
        "Graph representations, traversal, and shortest paths.",
        [
            ("Graph representations and modelling", 60, "medium", []),
            ("Breadth-first and depth-first search", 90, "medium", []),
            ("Shortest paths: Dijkstra and Bellman-Ford", 120, "hard", []),
        ],
    ),
]


def seed_defaults() -> None:
    if settings.is_production or not settings.seed_demo_data:
        return

    semester_start = _next_monday()
    semester_end = semester_start + timedelta(weeks=16) - timedelta(days=1)

    with session_scope() as db:
        existing_users = int(db.execute(select(func.count()).select_from(User)).scalar_one())
        if existing_users:
            return

        professor = User(
            name=settings.demo_professor_name,
            email=settings.demo_professor_email.lower(),
            password_hash=hash_password(settings.demo_professor_password),
            role=UserRole.PROFESSOR,
        )
        db.add(professor)
        db.flush()

        course = Course(
            name=settings.demo_course_name,
            course_code=settings.demo_course_code,
            professor_id=professor.id,
            department="Computer Science",
            semester="Fall",
            academic_year="2026",
            start_date=semester_start,
            end_date=semester_end,
            timezone="UTC",
            status=CourseStatus.DRAFT,
            lecture_duration_minutes=60,
            total_weeks=16,
            settings={
                "auto_publish_reschedules": False,
                "preserve_future_lectures": True,
                "combine_short_topics": True,
                "min_allocation_minutes": 5,
                "notes": "Demo course created at first start-up.",
            },
        )
        db.add(course)
        db.flush()

        # Two teaching windows per week, each fitting two 60-minute lectures.
        for weekday, start_hour, end_hour in ((0, 9, 11), (2, 9, 11)):
            db.add(
                AvailabilityRule(
                    course_id=course.id,
                    weekday=weekday,
                    start_time=time(start_hour, 0),
                    end_time=time(end_hour, 0),
                    room="Room 204",
                )
            )

        db.add(
            CalendarException(
                course_id=course.id,
                date=semester_start + timedelta(weeks=4),
                exception_type=ExceptionType.HOLIDAY,
                description="Founders' Day holiday",
                blocks_teaching=True,
            )
        )

        topic_by_title: dict[str, Topic] = {}
        for unit_index, (title, description, topics) in enumerate(DEMO_UNITS, start=1):
            unit = SyllabusUnit(
                course_id=course.id,
                title=title,
                description=description,
                sequence_number=unit_index,
            )
            db.add(unit)
            db.flush()
            for topic_index, (topic_title, minutes, difficulty, _) in enumerate(topics, start=1):
                topic = Topic(
                    unit_id=unit.id,
                    title=topic_title,
                    description="",
                    learning_objectives=[],
                    estimated_duration_minutes=minutes,
                    difficulty=difficulty,
                    sequence_number=topic_index,
                    priority=3,
                    is_splittable=True,
                    extraction_confidence=1.0,
                    needs_review=False,
                )
                db.add(topic)
                db.flush()
                topic_by_title[topic_title] = topic

        dependencies = [
            ("Linked lists and pointer discipline", "Arrays, strings, and dynamic arrays"),
            ("Binary search trees", "Algorithm analysis and asymptotic notation"),
            ("Tree traversals and recursion patterns", "Binary search trees"),
            (
                "Balanced trees: AVL and red-black overview",
                "Tree traversals and recursion patterns",
            ),
            ("Graph representations and modelling", "Linked lists and pointer discipline"),
            ("Breadth-first and depth-first search", "Graph representations and modelling"),
            ("Shortest paths: Dijkstra and Bellman-Ford", "Breadth-first and depth-first search"),
        ]
        for topic_title, prerequisite_title in dependencies:
            dependent = topic_by_title.get(topic_title)
            prerequisite = topic_by_title.get(prerequisite_title)
            if dependent is None or prerequisite is None:
                continue
            db.add(
                TopicDependency(
                    topic_id=dependent.id,
                    prerequisite_topic_id=prerequisite.id,
                    dependency_type=DependencyType.PREREQUISITE,
                )
            )

        logger.info(
            "Seeded demo professor %s with course '%s'.",
            settings.demo_professor_email,
            course.name,
        )


def _next_monday() -> date:
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7)
