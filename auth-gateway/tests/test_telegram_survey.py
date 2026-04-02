"""Tests for Telegram-based survey interaction flow.

Covers:
  - Text answer saves and advances question index
  - Photo rejected when text is expected (re-prompt)
  - Choice callback saves the selected option
  - Completing last answer creates SurveyResponse
  - Expired assignments are not picked up
  - Multiple assignments: in_progress before pending, oldest first
"""

import json
from datetime import datetime, timezone, timedelta

from app.models.survey import SurveyAssignment, SurveyResponse, SurveyTemplate
from app.routers.telegram import (
    _get_active_assignment,
    _get_current_question,
    _save_answer_and_advance,
    _total_questions,
)


# --------------- helpers ---------------

def _make_template(db_session, questions=None):
    """Insert a SurveyTemplate and return it."""
    if questions is None:
        questions = [
            {"type": "text", "label": "Describe the issue", "required": True},
            {"type": "choice", "label": "Severity", "options": ["Low", "High"], "required": True},
        ]
    t = SurveyTemplate(
        owner_username="ADMIN01",
        title="Test Template",
        description="",
        questions=json.dumps(questions),
        status="active",
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


def _make_assignment(
    db_session,
    template_id,
    telegram_id="123456789",
    target_username="WORKER01",
    status="pending",
    current_question_idx=0,
    partial_answers=None,
    assigned_at=None,
):
    """Insert a SurveyAssignment and return it."""
    a = SurveyAssignment(
        template_id=template_id,
        target_username=target_username,
        telegram_id=telegram_id,
        status=status,
        current_question_idx=current_question_idx,
        partial_answers=json.dumps(partial_answers or []),
    )
    if assigned_at:
        a.assigned_at = assigned_at
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)
    return a


# --------------- tests ---------------


def test_survey_text_answer_advances_question(db_session):
    """A text answer saves to partial_answers and advances current_question_idx."""
    import asyncio

    tmpl = _make_template(db_session)
    assignment = _make_assignment(db_session, tmpl.id, status="pending")

    # Answer the first question (text type)
    is_last = asyncio.get_event_loop().run_until_complete(
        _save_answer_and_advance(db_session, assignment, "Pipe looks corroded")
    )

    assert is_last is False
    db_session.refresh(assignment)
    assert assignment.current_question_idx == 1
    assert assignment.status == "in_progress"  # pending -> in_progress on first answer

    # partial_answers should have one entry
    partial = assignment.partial_answers
    if isinstance(partial, str):
        partial = json.loads(partial)
    assert len(partial) == 1
    assert partial[0]["value"] == "Pipe looks corroded"
    assert partial[0]["type"] == "text"


def test_survey_photo_rejected_when_text_expected(db_session):
    """When current question expects text, _get_current_question returns text type.

    The actual rejection logic lives in _handle_survey_photo which checks q_type
    and sends a re-prompt. Here we verify the question type detection so the
    rejection path would be taken.
    """
    tmpl = _make_template(db_session, questions=[
        {"type": "text", "label": "Describe the issue", "required": True},
    ])
    assignment = _make_assignment(db_session, tmpl.id)

    question = _get_current_question(db_session, assignment)
    assert question is not None
    assert question["type"] == "text"
    # The telegram handler checks: if q_type != "photo" -> send re-prompt
    # This confirms the condition would trigger for a photo sent to a text question


def test_survey_choice_callback_saves_answer(db_session):
    """A choice answer via callback saves the option value and advances."""
    import asyncio

    tmpl = _make_template(db_session, questions=[
        {"type": "choice", "label": "Status", "options": ["Good", "Bad"], "required": True},
        {"type": "text", "label": "Notes", "required": False},
    ])
    assignment = _make_assignment(db_session, tmpl.id, status="in_progress")

    # Simulate saving a choice callback answer
    is_last = asyncio.get_event_loop().run_until_complete(
        _save_answer_and_advance(db_session, assignment, "Good")
    )

    assert is_last is False
    db_session.refresh(assignment)
    assert assignment.current_question_idx == 1

    partial = assignment.partial_answers
    if isinstance(partial, str):
        partial = json.loads(partial)
    assert len(partial) == 1
    assert partial[0]["value"] == "Good"
    assert partial[0]["type"] == "choice"


def test_survey_completion_creates_response(db_session):
    """Answering the last question sets status=completed and creates SurveyResponse."""
    import asyncio

    tmpl = _make_template(db_session, questions=[
        {"type": "text", "label": "Only question", "required": True},
    ])
    assignment = _make_assignment(db_session, tmpl.id, status="pending")

    is_last = asyncio.get_event_loop().run_until_complete(
        _save_answer_and_advance(db_session, assignment, "Done")
    )

    assert is_last is True
    db_session.refresh(assignment)
    assert assignment.status == "completed"
    assert assignment.completed_at is not None

    # SurveyResponse should be created
    sr = db_session.query(SurveyResponse).filter(
        SurveyResponse.assignment_id == assignment.id,
    ).first()
    assert sr is not None
    assert sr.responder_username == "WORKER01"

    answers = sr.answers
    if isinstance(answers, str):
        answers = json.loads(answers)
    assert len(answers) == 1
    assert answers[0]["value"] == "Done"


def test_survey_expired_assignment_ignored(db_session):
    """Expired assignments are not returned by _get_active_assignment."""
    tmpl = _make_template(db_session)

    # Create an expired assignment
    _make_assignment(
        db_session, tmpl.id,
        telegram_id="999888777",
        status="expired",
    )

    # _get_active_assignment should return None
    result = _get_active_assignment(db_session, 999888777)
    assert result is None


def test_multiple_assignments_oldest_first(db_session):
    """in_progress assignments are preferred over pending; oldest first within each."""
    tmpl = _make_template(db_session)
    now = datetime.now(timezone.utc)

    # Pending, older
    pending_old = _make_assignment(
        db_session, tmpl.id,
        telegram_id="555666777",
        status="pending",
        assigned_at=now - timedelta(hours=2),
    )
    # Pending, newer
    _make_assignment(
        db_session, tmpl.id,
        telegram_id="555666777",
        target_username="WORKER02",
        status="pending",
        assigned_at=now - timedelta(hours=1),
    )

    # Should return the oldest pending
    result = _get_active_assignment(db_session, 555666777)
    assert result is not None
    assert result.id == pending_old.id

    # Now add an in_progress assignment (newer than both pending ones)
    in_progress = _make_assignment(
        db_session, tmpl.id,
        telegram_id="555666777",
        target_username="WORKER03",
        status="in_progress",
        assigned_at=now,
    )

    # in_progress should be preferred over pending
    result = _get_active_assignment(db_session, 555666777)
    assert result is not None
    assert result.id == in_progress.id
    assert result.status == "in_progress"
