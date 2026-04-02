"""Tests for survey CRUD API endpoints.

Covers:
  - Creating a survey template with valid questions
  - Rejecting empty questions list and invalid question type
  - Listing surveys includes response_count
  - Getting a single survey by ID
  - Assigning a survey to users
  - Retrieving survey responses
"""

import json

from app.models.survey import SurveyAssignment, SurveyResponse


# --------------- helpers ---------------

_VALID_QUESTIONS = [
    {"type": "text", "label": "Describe the issue", "required": True},
    {"type": "photo", "label": "Take a photo", "required": True},
    {"type": "choice", "label": "Severity", "options": ["Low", "Medium", "High"], "required": True},
]


# --------------- tests ---------------


def test_create_survey_template(client, create_test_user):
    """POST /api/v1/surveys with valid questions returns 201 and persists."""
    create_test_user(username="TESTUSER01")

    resp = client.post("/api/v1/surveys", json={
        "title": "Field Inspection",
        "description": "Daily inspection form",
        "questions": _VALID_QUESTIONS,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Field Inspection"
    assert data["owner_username"] == "TESTUSER01"
    assert len(data["questions"]) == 3
    assert data["response_count"] == 0
    assert data["status"] == "active"


def test_create_survey_invalid_questions(client, create_test_user):
    """POST /api/v1/surveys rejects empty questions and invalid type."""
    create_test_user(username="TESTUSER01")

    # Empty questions list
    resp = client.post("/api/v1/surveys", json={
        "title": "Bad Survey",
        "questions": [],
    })
    assert resp.status_code == 422  # Pydantic validation error

    # Invalid question type
    resp = client.post("/api/v1/surveys", json={
        "title": "Bad Survey",
        "questions": [{"type": "video", "label": "Record a video", "required": True}],
    })
    assert resp.status_code == 422


def test_list_surveys_with_response_count(
    client, db_session, create_test_user,
    create_test_survey_template, create_test_assignment,
):
    """GET /api/v1/surveys returns templates with accurate response_count."""
    create_test_user(username="TESTUSER01")

    tmpl = create_test_survey_template(owner_username="TESTUSER01", title="Survey A")
    tmpl_id = tmpl.id

    # Create an assignment and a completed response for it
    assignment = create_test_assignment(
        template_id=tmpl_id,
        target_username="WORKER01",
        status="completed",
    )
    response = SurveyResponse(
        assignment_id=assignment.id,
        responder_username="WORKER01",
        answers=json.dumps([{"question_idx": 0, "type": "text", "value": "All good"}]),
    )
    db_session.add(response)
    db_session.commit()

    # Also create a template with no responses
    create_test_survey_template(
        owner_username="TESTUSER01", title="Survey B",
    )

    resp = client.get("/api/v1/surveys")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    # Results are ordered by created_at desc, so Survey B is first
    survey_b = next(s for s in data if s["title"] == "Survey B")
    survey_a = next(s for s in data if s["title"] == "Survey A")
    assert survey_b["response_count"] == 0
    assert survey_a["response_count"] == 1


def test_get_survey_detail(client, create_test_user, create_test_survey_template):
    """GET /api/v1/surveys/{id} returns the correct template."""
    create_test_user(username="TESTUSER01")
    tmpl = create_test_survey_template(
        owner_username="TESTUSER01", title="Detail Test",
    )

    resp = client.get(f"/api/v1/surveys/{tmpl.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == tmpl.id
    assert data["title"] == "Detail Test"
    assert data["response_count"] == 0

    # Non-existent ID returns 404
    resp = client.get("/api/v1/surveys/99999")
    assert resp.status_code == 404


def test_assign_survey(
    client, db_session, create_test_user,
    create_test_survey_template, create_test_telegram_mapping,
):
    """POST /api/v1/surveys/{id}/assign creates assignments with telegram_id lookup."""
    create_test_user(username="TESTUSER01")
    create_test_telegram_mapping(
        telegram_id=111222333, username="WORKER01",
    )

    tmpl = create_test_survey_template(owner_username="TESTUSER01")

    resp = client.post(f"/api/v1/surveys/{tmpl.id}/assign", json={
        "target_usernames": ["worker01", "WORKER02"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert len(data) == 2

    # WORKER01 has telegram mapping, WORKER02 does not
    worker01 = next(a for a in data if a["target_username"] == "WORKER01")
    worker02 = next(a for a in data if a["target_username"] == "WORKER02")
    assert worker01["telegram_id"] == "111222333"
    assert worker01["status"] == "pending"
    assert worker02["telegram_id"] is None


def test_get_survey_responses(
    client, db_session, create_test_user,
    create_test_survey_template, create_test_assignment,
):
    """GET /api/v1/surveys/{id}/responses returns completed responses."""
    create_test_user(username="TESTUSER01")
    tmpl = create_test_survey_template(owner_username="TESTUSER01")

    # Create a completed assignment + response
    assignment = create_test_assignment(
        template_id=tmpl.id,
        target_username="WORKER01",
        status="completed",
    )
    answers = [
        {"question_idx": 0, "type": "text", "value": "Pipe corroded"},
        {"question_idx": 1, "type": "choice", "value": "Bad"},
    ]
    sr = SurveyResponse(
        assignment_id=assignment.id,
        responder_username="WORKER01",
        answers=json.dumps(answers),
    )
    db_session.add(sr)
    db_session.commit()

    resp = client.get(f"/api/v1/surveys/{tmpl.id}/responses")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["responder_username"] == "WORKER01"
    assert len(data[0]["answers"]) == 2

    # No responses for non-existent survey
    resp = client.get("/api/v1/surveys/99999/responses")
    assert resp.status_code == 404
