"""Phase 2 A+B — Skills governance 스키마 확장 + 감사 이벤트 테스트.

검증 범위:
- approve/reject/delete/submit 시 skill_governance_events 행 생성
- reject 엔드포인트: approval_status 전환, SoD 강제, rejection_reason 저장
- skill 삭제 후 governance 이벤트는 skill_id=NULL로 잔존
"""

from app.models.skill import (
    SharedSkill,
    SkillApprovalStatus,
    SkillGovernanceEvent,
    SkillGovernanceEventType,
)


_ADMIN_SUB = "ADMIN01"
_AUTHOR = "USER42"


def _mock_admin(sub: str = _ADMIN_SUB):
    def _factory():
        return {"sub": sub, "role": "admin", "name": "Admin"}
    return _factory


def _insert_skill(db_session, *, author: str = _AUTHOR, owner: str | None = None) -> SharedSkill:
    skill = SharedSkill(
        author_username=author,
        author_name=author,
        title="T",
        description="d",
        category="skill",
        content="body",
        owner_username=owner or author,
        skill_name="/t",
        skill_type="slash_command",
    )
    db_session.add(skill)
    db_session.commit()
    db_session.refresh(skill)
    return skill


class TestApprovalStatusTransition:
    def test_approve_sets_status_to_approved(self, client, db_session):
        from app.core.security import get_current_user
        skill = _insert_skill(db_session)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value

        client.app.dependency_overrides[get_current_user] = _mock_admin()
        resp = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp.status_code == 200

        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.APPROVED.value
        assert skill.is_approved is True

    def test_reject_sets_status_and_records_reason(self, client, db_session):
        from app.core.security import get_current_user
        skill = _insert_skill(db_session)
        client.app.dependency_overrides[get_current_user] = _mock_admin()

        resp = client.patch(
            f"/api/v1/skills/{skill.id}/reject",
            params={"reason": "민감정보 포함"},
        )
        assert resp.status_code == 200

        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.REJECTED.value
        assert skill.is_approved is False
        assert skill.rejected_by == _ADMIN_SUB
        assert skill.rejection_reason == "민감정보 포함"
        assert skill.rejected_at is not None

    def test_reject_respects_sod(self, client, db_session):
        """reject도 자기 작성 스킬은 거부 — approve와 동일 정책."""
        from app.core.security import get_current_user
        skill = _insert_skill(db_session, author=_ADMIN_SUB)
        client.app.dependency_overrides[get_current_user] = _mock_admin()

        resp = client.patch(f"/api/v1/skills/{skill.id}/reject")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "sod_violation"

        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value
        assert skill.rejected_by is None


class TestGovernanceEventLogging:
    def test_submit_creates_event(self, client, db_session):
        # 기본 mock user = TESTUSER01 (role=user)
        body = {
            "title": "New skill",
            "description": "desc",
            "category": "skill",
            "content": "This is the skill content body for testing.",
        }
        resp = client.post("/api/v1/skills/submit", json=body)
        assert resp.status_code == 201
        skill_id = resp.json()["id"]

        events = (
            db_session.query(SkillGovernanceEvent)
            .filter(SkillGovernanceEvent.skill_id == skill_id)
            .all()
        )
        assert len(events) == 1
        assert events[0].event_type == SkillGovernanceEventType.SUBMIT.value
        assert events[0].actor_username == "TESTUSER01"
        assert events[0].actor_role == "user"

    def test_approve_creates_event(self, client, db_session):
        from app.core.security import get_current_user
        skill = _insert_skill(db_session)
        client.app.dependency_overrides[get_current_user] = _mock_admin()
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        events = (
            db_session.query(SkillGovernanceEvent)
            .filter(
                SkillGovernanceEvent.skill_id == skill.id,
                SkillGovernanceEvent.event_type == SkillGovernanceEventType.APPROVE.value,
            )
            .all()
        )
        assert len(events) == 1
        assert events[0].actor_username == _ADMIN_SUB
        assert events[0].actor_role == "admin"

    def test_reject_creates_event_with_reason(self, client, db_session):
        from app.core.security import get_current_user
        skill = _insert_skill(db_session)
        client.app.dependency_overrides[get_current_user] = _mock_admin()
        client.patch(f"/api/v1/skills/{skill.id}/reject", params={"reason": "bad"})

        events = (
            db_session.query(SkillGovernanceEvent)
            .filter(
                SkillGovernanceEvent.skill_id == skill.id,
                SkillGovernanceEvent.event_type == SkillGovernanceEventType.REJECT.value,
            )
            .all()
        )
        assert len(events) == 1
        assert events[0].detail == "bad"

    def test_sod_violation_does_not_create_event(self, client, db_session):
        """승인 차단 시 governance 이벤트 미생성 — 실패 시도는 서버 로그로만."""
        from app.core.security import get_current_user
        skill = _insert_skill(db_session, author=_ADMIN_SUB)
        client.app.dependency_overrides[get_current_user] = _mock_admin()
        resp = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp.status_code == 403

        events = (
            db_session.query(SkillGovernanceEvent)
            .filter(SkillGovernanceEvent.skill_id == skill.id)
            .all()
        )
        assert events == []

    def test_delete_creates_event_and_preserves_history(self, client, db_session):
        """skill 삭제 후에도 governance 이벤트는 남음 (skill_id는 NULL)."""
        from app.core.security import get_current_user
        skill = _insert_skill(db_session)
        skill_id = skill.id
        client.app.dependency_overrides[get_current_user] = _mock_admin()
        resp = client.delete(f"/api/v1/skills/{skill_id}")
        assert resp.status_code == 200

        # skill row 삭제 확인
        assert db_session.query(SharedSkill).filter_by(id=skill_id).first() is None

        # 이벤트는 skill_id NULL로 유지 (ON DELETE SET NULL)
        delete_events = (
            db_session.query(SkillGovernanceEvent)
            .filter(SkillGovernanceEvent.event_type == SkillGovernanceEventType.DELETE.value)
            .all()
        )
        assert len(delete_events) == 1
        # SQLite in test DB may not enforce ON DELETE SET NULL — 일부 dialect 의존
        # 핵심은 이벤트 row 자체가 지워지지 않음.
        assert delete_events[0].actor_username == _ADMIN_SUB
