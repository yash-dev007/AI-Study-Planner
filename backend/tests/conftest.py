"""Shared test fixtures.

Environment variables must be set *before* ``app`` is imported because settings
are cached at import time. Tests therefore configure a throwaway SQLite database
in a temporary directory, disable the LLM (rule-based extraction only), and turn
the demo seed off.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

TEMP_ROOT = Path(tempfile.mkdtemp(prefix="adaptive-syllabus-tests-"))

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{TEMP_ROOT / 'test.db'}"
os.environ["STORAGE_DIR"] = str(TEMP_ROOT / "storage")
os.environ["JWT_SECRET"] = "test-secret-value-that-is-long-enough-for-hs256"
os.environ["LLM_PROVIDER"] = "none"
os.environ["SEED_DEMO_DATA"] = "false"
# Tests make many requests; keep throttling out of the way.
os.environ["RATE_LIMIT_AUTH_PER_MINUTE"] = "100000"
os.environ["RATE_LIMIT_UPLOAD_PER_MINUTE"] = "100000"
os.environ["RATE_LIMIT_DEFAULT_PER_MINUTE"] = "1000000"

import pytest  # noqa: E402
from app.ai.schemas import ExtractedTopic, ExtractedUnit, SyllabusExtraction  # noqa: E402
from app.ai.service import SyllabusExtractionService  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

# --------------------------------------------------------------------------- #
# Fake LLM provider: deterministic, offline, and swappable.
# --------------------------------------------------------------------------- #


class MockLLMInterpreter:
    """A stand-in for a hosted or local LLM.

    ``mode`` controls the behaviour under test: a well-formed response, a
    malformed response, or a hard failure.
    """

    name = "mock-llm"

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.calls: list[str] = []

    def extract(self, text: str) -> SyllabusExtraction:
        self.calls.append(text)
        if self.mode == "raise":
            from app.ai.llm import LLMError

            raise LLMError("provider unavailable")
        if self.mode == "boom":
            raise RuntimeError("provider crashed in an unexpected way")
        if self.mode == "malformed":
            raise ValueError("could not parse provider payload")
        return SyllabusExtraction(
            units=[
                ExtractedUnit(
                    key="unit-1",
                    title="LLM Unit: Processes",
                    description="Processes and threads.",
                    sequence_number=1,
                    topics=[
                        ExtractedTopic(
                            key="t1",
                            title="Process lifecycle",
                            description="States, PCB, context switching.",
                            learning_objectives=["Describe process states"],
                            estimated_duration_minutes=90,
                            difficulty="medium",
                            confidence=0.9,
                        ),
                        ExtractedTopic(
                            key="t2",
                            title="Threads and concurrency",
                            description="Thread models.",
                            estimated_duration_minutes=90,
                            difficulty="hard",
                            confidence=0.88,
                            prerequisite_keys=["t1"],
                        ),
                    ],
                )
            ],
            confidence=0.9,
        )


@pytest.fixture()
def mock_llm() -> MockLLMInterpreter:
    return MockLLMInterpreter()


@pytest.fixture()
def extraction_service(mock_llm: MockLLMInterpreter) -> SyllabusExtractionService:
    return SyllabusExtractionService(settings, llm=mock_llm)


@pytest.fixture()
def failing_llm_service() -> SyllabusExtractionService:
    return SyllabusExtractionService(settings, llm=MockLLMInterpreter(mode="raise"))


# --------------------------------------------------------------------------- #
# Database / client
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def fresh_database() -> Iterator[None]:
    """A brand-new SQLite file per test.

    ``drop_all`` cannot order the tables because the schedule tables reference
    each other (a revision points at its triggering lecture), so the file is
    removed instead.
    """

    _reset_database_file()
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    _reset_database_file()


def _reset_database_file() -> None:
    engine.dispose()
    for path in TEMP_ROOT.glob("test.db*"):
        path.unlink(missing_ok=True)


@pytest.fixture()
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(fresh_database: None) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #


def _register(
    client: TestClient, *, email: str, password: str = "ProfPassw0rd!", name: str = "Prof"
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": password, "role": "professor"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def professor_headers(client: TestClient) -> dict[str, str]:
    return _register(client, email=f"prof-{uuid.uuid4().hex[:8]}@example.edu")


@pytest.fixture()
def other_professor_headers(client: TestClient) -> dict[str, str]:
    return _register(client, email=f"other-{uuid.uuid4().hex[:8]}@example.edu")


# --------------------------------------------------------------------------- #
# Course fixtures
# --------------------------------------------------------------------------- #

SEMESTER_WEEKS = 8


def semester_start() -> date:
    """The next Monday, so generated slots are always in the future."""

    today = date.today()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        # Today is Monday: its lecture times may already have passed.
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def course_payload(**overrides: Any) -> dict[str, Any]:
    start = semester_start()
    payload: dict[str, Any] = {
        "name": "Operating Systems",
        "course_code": "CS301",
        "department": "Computer Science",
        "semester": "Fall",
        "academic_year": "2026",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(weeks=SEMESTER_WEEKS) - timedelta(days=1)).isoformat(),
        "timezone": "UTC",
        "lecture_duration_minutes": 60,
        "total_weeks": SEMESTER_WEEKS,
        # Monday 09:00-12:00 gives three 60-minute slots per week.
        "availability_rules": [
            {"weekday": 0, "start_time": "09:00:00", "end_time": "12:00:00", "room": "Room 1"}
        ],
        "exceptions": [],
        "settings": {
            "auto_publish_reschedules": False,
            "preserve_future_lectures": True,
            "combine_short_topics": True,
            "min_allocation_minutes": 5,
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def course(client: TestClient, professor_headers: dict[str, str]) -> dict[str, Any]:
    response = client.post("/api/v1/courses", json=course_payload(), headers=professor_headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def course_id(course: dict[str, Any]) -> str:
    return str(course["id"])


# --------------------------------------------------------------------------- #
# Syllabus fixtures
# --------------------------------------------------------------------------- #

SYLLABUS_TEXT = """\
CS301 Operating Systems - Syllabus
Department of Computer Science, Fall 2026

Course objectives: understand processes, memory, and file systems.

Unit 1: Process Management
Topics:
1. Process lifecycle, states, and the PCB (90 minutes)
2. Threads and concurrency models (60 minutes)
3. CPU scheduling algorithms (120 minutes)

Unit 2: Memory Management
Topics:
1. Contiguous memory allocation (60 minutes)
2. Paging and segmentation (90 minutes)
3. Virtual memory and page replacement (120 minutes)

Unit 3: File Systems
Topics:
1. File system interface and operations (60 minutes)
2. Disk scheduling (60 minutes)

Evaluation: two midterms, one final, and four assignments.
"""

CONFIRM_UNITS: list[dict[str, Any]] = [
    {
        "key": "unit-1",
        "title": "Unit 1: Process Management",
        "description": "",
        "sequence_number": 1,
        "topics": [
            {
                "key": "t1",
                "title": "Process lifecycle, states, and the PCB",
                "description": "",
                "learning_objectives": [],
                "estimated_duration_minutes": 90,
                "difficulty": "medium",
                "confidence": 0.9,
                "needs_review": False,
                "prerequisite_keys": [],
                "sequence_number": 1,
            },
            {
                "key": "t2",
                "title": "Threads and concurrency models",
                "description": "",
                "learning_objectives": [],
                "estimated_duration_minutes": 60,
                "difficulty": "medium",
                "confidence": 0.9,
                "needs_review": False,
                "prerequisite_keys": ["t1"],
                "sequence_number": 2,
            },
            {
                "key": "t3",
                "title": "CPU scheduling algorithms",
                "description": "",
                "learning_objectives": [],
                "estimated_duration_minutes": 120,
                "difficulty": "hard",
                "confidence": 0.9,
                "needs_review": False,
                "prerequisite_keys": ["t2"],
                "sequence_number": 3,
            },
        ],
    },
    {
        "key": "unit-2",
        "title": "Unit 2: Memory Management",
        "description": "",
        "sequence_number": 2,
        "topics": [
            {
                "key": "t4",
                "title": "Contiguous memory allocation",
                "description": "",
                "learning_objectives": [],
                "estimated_duration_minutes": 60,
                "difficulty": "medium",
                "confidence": 0.9,
                "needs_review": False,
                "prerequisite_keys": [],
                "sequence_number": 1,
            },
            {
                "key": "t5",
                "title": "Paging and segmentation",
                "description": "",
                "learning_objectives": [],
                "estimated_duration_minutes": 90,
                "difficulty": "medium",
                "confidence": 0.9,
                "needs_review": False,
                "prerequisite_keys": ["t4"],
                "sequence_number": 2,
            },
        ],
    },
]


def syllabus_bytes(text: str = SYLLABUS_TEXT, name: str = "syllabus.txt") -> tuple[str, bytes, str]:
    return (name, text.encode("utf-8"), "text/plain")


def upload_syllabus(
    client: TestClient, course_id: str, headers: dict[str, str], text: str = SYLLABUS_TEXT
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/courses/{course_id}/syllabus/upload",
        files={"file": syllabus_bytes(text)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirm_syllabus(
    client: TestClient,
    course_id: str,
    headers: dict[str, str],
    units: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/courses/{course_id}/syllabus/confirm",
        json={"units": units or CONFIRM_UNITS, **extra},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def ready_course(
    client: TestClient, course_id: str, professor_headers: dict[str, str]
) -> dict[str, Any]:
    """A course with a confirmed syllabus, ready to be scheduled."""

    upload_syllabus(client, course_id, professor_headers)
    structure = confirm_syllabus(client, course_id, professor_headers)
    return {"course_id": course_id, "structure": structure}


@pytest.fixture()
def scheduled_course(
    client: TestClient, ready_course: dict[str, Any], professor_headers: dict[str, str]
) -> dict[str, Any]:
    """A course with an approved, published schedule."""

    course_id = ready_course["course_id"]
    generated = client.post(
        f"/api/v1/courses/{course_id}/schedule/generate",
        json={"strategy": "preserve_future"},
        headers=professor_headers,
    )
    assert generated.status_code == 201, generated.text
    schedule = generated.json()
    revision_id = schedule["revision"]["id"]
    approved = client.post(
        f"/api/v1/courses/{course_id}/schedule/revisions/{revision_id}/approve",
        json={"publish": True, "note": "Approved in test"},
        headers=professor_headers,
    )
    assert approved.status_code == 200, approved.text
    return {
        **ready_course,
        "schedule": approved.json(),
        "revision_id": revision_id,
    }


def course_timezone_offset() -> int:
    return 0


def fixed_datetime(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=minute))
