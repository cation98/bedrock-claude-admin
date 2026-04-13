"""Phase 2 skills-admin-ui T1 — 승인 진행률 API 테스트.

검증 범위:
- GET /api/v1/skills/pending-progress
    · approval_status='pending' 스킬만 반환 (approved/rejected 제외)
    · current_approvals / required_approvals 정확성
    · 빈 결과 / 비관리자 403
- GET /api/v1/skills/{skill_id}/approval-progress
    · 0명 승인 상태에서 can_approve=True
    · SoD 차단(author=admin) → sod_blocked=True, can_approve=False
    · 이미 승인한 admin → can_approve=False
    · approval_status=approved / rejected → can_approve=False
    · current_approvers 목록 created_at 오름차순 정렬
    · rejection_reason 필드 포함
    · 비관리자 403
- 404: 존재하지 않는 skill_id
"""

from app.models.skill import (
    SharedSkill,
    SkillApprovalPolicy,
    SkillApprovalStatus,
    SkillGovernanceEvent,
    SkillGovernanceEventType,
)

_ADMIN1 = "ADMIN01"
_ADMIN2 = "ADMIN02"
_AUTHOR = "USER42"


def _mock_admin(sub: str = _ADMIN1):
    return lambda: {"sub": sub, "role": "admin", "name": "Admin"}


def _insert_skill(
    db_session,
    *,
    author: str = _AUTHOR,
    owner: str | None = None,
    category: str = "skill",
    approval_status: str = SkillApprovalStatus.PENDING.value,
    title: str = "Test Skill",
) -> SharedSkill:
    """테스트용 SharedSkill 행 삽입."""
    s = SharedSkill(
        author_username=author,
        author_name=author,
        title=title,
        description="desc",
        category=category,
        content="body",
        owner_username=owner or author,
        skill_name="/test",
        skill_type="slash_command",
        approval_status=approval_status,
        is_approved=(approval_status == SkillApprovalStatus.APPROVED.value),
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


def _insert_policy(db_session, category: str, required: int) -> None:
    """카테고리별 N-of-M 정책 행 삽입 (이미 있으면 업데이트)."""
    existing = db_session.query(SkillApprovalPolicy).filter_by(category=category).first()
    if existing:
        existing.required_approvals = required
    else:
        db_session.add(SkillApprovalPolicy(category=category, required_approvals=required))
    db_session.commit()


# ---------------------------------------------------------------------------
# 1. GET /api/v1/skills/pending-progress
# ---------------------------------------------------------------------------


class TestPendingProgress:
    """pending-progress 목록: approval_status='pending' 스킬만 반환."""

    def test_returns_only_pending_skills(self, client, db_session):
        """pending 2개 + approved 1개 + rejected 1개 → pending 2개만 반환."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        s1 = _insert_skill(db_session, title="Pending1")
        s2 = _insert_skill(db_session, title="Pending2")
        _insert_skill(db_session, title="Approved", approval_status=SkillApprovalStatus.APPROVED.value)
        _insert_skill(db_session, title="Rejected", approval_status=SkillApprovalStatus.REJECTED.value)

        client.app.dependency_overrides[get_current_user] = _mock_admin()
        resp = client.get("/api/v1/skills/pending-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        returned_ids = {item["skill_id"] for item in data}
        assert returned_ids == {s1.id, s2.id}

    def test_current_and_required_approvals_correct(self, client, db_session):
        """ADMIN1이 1회 승인(pending 유지) → current_approvals=1, required_approvals=2."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session)

        # ADMIN1 승인 → pending 유지 (정족수 미달)
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        resp = client.get("/api/v1/skills/pending-progress")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        item = data[0]
        assert item["skill_id"] == skill.id
        assert item["current_approvals"] == 1
        assert item["required_approvals"] == 2
        assert item["approval_status"] == "pending"

    def test_no_pending_returns_empty_list(self, client, db_session):
        """pending 스킬 없으면 빈 배열 반환."""
        from app.core.security import get_current_user

        _insert_skill(db_session, approval_status=SkillApprovalStatus.APPROVED.value)
        client.app.dependency_overrides[get_current_user] = _mock_admin()
        resp = client.get("/api/v1/skills/pending-progress")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_non_admin_access_returns_403(self, client, db_session):
        """일반 사용자(role=user)는 403 반환."""
        # conftest 기본 mock은 role=user
        resp = client.get("/api/v1/skills/pending-progress")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. GET /api/v1/skills/{skill_id}/approval-progress
# ---------------------------------------------------------------------------


class TestApprovalProgressDetail:
    """approval-progress 상세: 승인 진행 상태·can_approve·sod 여부 반환."""

    def test_pending_zero_approvers_can_approve(self, client, db_session):
        """pending + 0명 승인 → current_approvers=[], can_approve=True (다른 admin)."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session)

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["skill_id"] == skill.id
        assert data["approval_status"] == "pending"
        assert data["current_approvers"] == []
        assert data["required_approvals"] == 2
        assert data["can_current_admin_approve"] is True
        assert data["sod_blocked"] is False

    def test_sod_blocked_cannot_approve(self, client, db_session):
        """author == admin → sod_blocked=True, can_approve=False."""
        from app.core.security import get_current_user

        skill = _insert_skill(db_session, author=_ADMIN1, owner=_ADMIN1)

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["sod_blocked"] is True
        assert data["can_current_admin_approve"] is False

    def test_sod_blocked_by_owner_username(self, client, db_session):
        """author는 다르지만 owner == admin → sod_blocked=True."""
        from app.core.security import get_current_user

        skill = _insert_skill(db_session, author=_AUTHOR, owner=_ADMIN1)

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["sod_blocked"] is True
        assert data["can_current_admin_approve"] is False

    def test_already_approved_cannot_approve_again(self, client, db_session):
        """이미 APPROVE 이벤트가 있는 admin → can_approve=False."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session)

        # ADMIN1 승인 (pending 유지)
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        # ADMIN1이 진행률 조회 → can_approve=False
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["can_current_admin_approve"] is False
        assert len(data["current_approvers"]) == 1
        assert data["current_approvers"][0]["username"] == _ADMIN1

    def test_approved_skill_cannot_approve(self, client, db_session):
        """approval_status=approved → can_approve=False."""
        from app.core.security import get_current_user

        skill = _insert_skill(db_session, approval_status=SkillApprovalStatus.APPROVED.value)

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["approval_status"] == "approved"
        assert data["can_current_admin_approve"] is False

    def test_rejected_skill_cannot_approve(self, client, db_session):
        """approval_status=rejected → can_approve=False."""
        from app.core.security import get_current_user

        skill = _insert_skill(db_session, approval_status=SkillApprovalStatus.REJECTED.value)

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["approval_status"] == "rejected"
        assert data["can_current_admin_approve"] is False

    def test_approver_list_ordered_by_created_at(self, client, db_session):
        """current_approvers는 created_at 오름차순 — ADMIN1이 먼저, ADMIN2가 나중."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 3)
        skill = _insert_skill(db_session)

        # ADMIN1 먼저 승인
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        # ADMIN2 다음 승인
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        # ADMIN2가 진행률 조회
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["current_approvers"]) == 2
        assert data["current_approvers"][0]["username"] == _ADMIN1
        assert data["current_approvers"][1]["username"] == _ADMIN2

    def test_rejection_reason_included(self, client, db_session):
        """rejection_reason 필드가 응답에 포함된다."""
        from app.core.security import get_current_user

        skill = _insert_skill(db_session, approval_status=SkillApprovalStatus.REJECTED.value)
        skill.rejection_reason = "품질 기준 미달"
        db_session.commit()

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")

        assert resp.status_code == 200
        assert resp.json()["rejection_reason"] == "품질 기준 미달"

    def test_non_admin_access_returns_403(self, client, db_session):
        """일반 사용자(role=user)는 403 반환."""
        skill = _insert_skill(db_session)
        resp = client.get(f"/api/v1/skills/{skill.id}/approval-progress")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. 404 케이스
# ---------------------------------------------------------------------------


class TestNonexistent:
    """존재하지 않는 skill_id 접근 시 404."""

    def test_approval_progress_nonexistent_returns_404(self, client, db_session):
        """DB에 없는 skill_id → 404."""
        from app.core.security import get_current_user

        client.app.dependency_overrides[get_current_user] = _mock_admin()
        resp = client.get("/api/v1/skills/99999/approval-progress")
        assert resp.status_code == 404
