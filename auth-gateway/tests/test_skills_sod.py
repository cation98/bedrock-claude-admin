"""Skills governance — Separation of Duties (SoD) 테스트.

Phase 1 백로그 #20: 스킬 작성자가 자기 제출 스킬을 승인하지 못하도록 서버에서
강제. 4-eyes 원칙 위반 시 403 + error=sod_violation.
"""

from app.models.skill import SharedSkill


_ADMIN_SUB = "ADMIN01"
_OTHER_USER = "USER99"


def _mock_admin(sub: str = _ADMIN_SUB):
    def _factory():
        return {"sub": sub, "role": "admin", "name": "Admin"}
    return _factory


def _insert_skill(db_session, *, author: str, owner: str | None = None) -> SharedSkill:
    skill = SharedSkill(
        author_username=author,
        author_name=author,
        title="Test Skill",
        description="desc",
        category="skill",
        content="# Skill body",
        owner_username=owner or author,
        skill_name="/test-skill",
        skill_type="slash_command",
    )
    db_session.add(skill)
    db_session.commit()
    db_session.refresh(skill)
    return skill


def test_admin_can_approve_other_users_skill(client, db_session):
    """다른 사용자가 제출한 스킬은 admin이 정상 승인 가능."""
    from app.core.security import get_current_user

    skill = _insert_skill(db_session, author=_OTHER_USER)
    client.app.dependency_overrides[get_current_user] = _mock_admin()

    resp = client.patch(f"/api/v1/skills/{skill.id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_approved"] is True
    assert data["approved_by"] == _ADMIN_SUB


def test_admin_cannot_approve_own_submitted_skill(client, db_session):
    """SoD: admin이 직접 제출한(author=admin) 스킬은 승인 거부 — 403."""
    from app.core.security import get_current_user

    skill = _insert_skill(db_session, author=_ADMIN_SUB)
    client.app.dependency_overrides[get_current_user] = _mock_admin()

    resp = client.patch(f"/api/v1/skills/{skill.id}/approve")
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["error"] == "sod_violation"
    # DB 상태 변경 없는지 확인
    db_session.refresh(skill)
    assert skill.is_approved is False
    assert skill.approved_by is None


def test_admin_cannot_approve_own_owned_skill(client, db_session):
    """SoD: author는 다르더라도 owner_username이 admin이면 차단 (스토어 퍼블리시 경로)."""
    from app.core.security import get_current_user

    skill = _insert_skill(db_session, author=_OTHER_USER, owner=_ADMIN_SUB)
    client.app.dependency_overrides[get_current_user] = _mock_admin()

    resp = client.patch(f"/api/v1/skills/{skill.id}/approve")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "sod_violation"


def test_non_admin_cannot_approve(client, db_session):
    """일반 사용자는 승인 불가 — 기존 권한 체크 유지 확인."""
    skill = _insert_skill(db_session, author=_OTHER_USER)
    # 기본 mock user는 role=user (conftest)
    resp = client.patch(f"/api/v1/skills/{skill.id}/approve")
    assert resp.status_code == 403


def test_approve_nonexistent_skill_returns_404(client):
    from app.core.security import get_current_user
    client.app.dependency_overrides[get_current_user] = _mock_admin()

    resp = client.patch("/api/v1/skills/99999/approve")
    assert resp.status_code == 404
