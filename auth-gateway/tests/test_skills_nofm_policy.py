"""Phase 2 E — N-of-M 정책 테스트 (필수 승인수 / 중복 차단 / 다중 승인 / SoD / reject).

검증 범위:
- skill_approval_policies 정책 기반 승인 정족수 적용
- 미정의 카테고리 → fallback=1 (DEFAULT_REQUIRED_APPROVALS)
- 동일 관리자 중복 승인 차단 (409 duplicate_approval)
- N명 distinct 관리자 승인 도달 시 approval_status='approved' 전환
- SoD 위반은 N-of-M 로직보다 우선 처리 (event 미생성)
- reject는 N-of-M와 무관하게 단일 관리자가 즉시 처리

주의: SkillResponse 스키마에 approval_status/rejected_by 미포함 →
      DB 상태는 db_session.refresh(skill)로 직접 검증한다.
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
_ADMIN3 = "ADMIN03"
_AUTHOR = "USER42"


def _mock_admin(sub: str):
    return lambda: {"sub": sub, "role": "admin", "name": "Admin"}


def _insert_skill(
    db_session,
    *,
    author: str = _AUTHOR,
    owner: str | None = None,
    category: str = "skill",
) -> SharedSkill:
    s = SharedSkill(
        author_username=author,
        author_name=author,
        title="T",
        description="d",
        category=category,
        content="body",
        owner_username=owner or author,
        skill_name="/t",
        skill_type="slash_command",
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


def _insert_policy(db_session, category: str, required: int) -> None:
    existing = db_session.query(SkillApprovalPolicy).filter_by(category=category).first()
    if existing:
        existing.required_approvals = required
    else:
        db_session.add(SkillApprovalPolicy(category=category, required_approvals=required))
    db_session.commit()


# ---------------------------------------------------------------------------
# 1. 미정의 카테고리 fallback
# ---------------------------------------------------------------------------


class TestRequiredApprovalsFallback:
    """skill_approval_policies 미등록 카테고리는 정족수=1 (DEFAULT_REQUIRED_APPROVALS) 적용."""

    def test_no_policy_row_fallback_to_one(self, client, db_session):
        """policy row 없는 카테고리도 단독 승인으로 approved 전환 — fallback 확인."""
        from app.core.security import get_current_user

        # policy row 없이 스킬 생성
        skill = _insert_skill(db_session, category="uncategorized-xyz")
        assert (
            db_session.query(SkillApprovalPolicy)
            .filter_by(category="uncategorized-xyz")
            .first()
        ) is None

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp = client.patch(f"/api/v1/skills/{skill.id}/approve")

        assert resp.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.APPROVED.value
        assert skill.is_approved is True
        assert skill.approved_by == _ADMIN1


# ---------------------------------------------------------------------------
# 2. required_approvals=1 — 단독 승인
# ---------------------------------------------------------------------------


class TestSingleApprover:
    """required_approvals=1 정책: 단독 승인으로 즉시 approved."""

    def test_single_approval_sets_approved(self, client, db_session):
        """policy=1 + admin1 1회 승인 → status='approved', is_approved=True."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 1)
        skill = _insert_skill(db_session, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp = client.patch(f"/api/v1/skills/{skill.id}/approve")

        assert resp.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.APPROVED.value
        assert skill.is_approved is True
        assert skill.approved_by == _ADMIN1

    def test_duplicate_approval_returns_409(self, client, db_session):
        """동일 admin이 이미 승인한 스킬에 재승인 시도 → 409 duplicate_approval."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 1)
        skill = _insert_skill(db_session, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)

        # 1차 승인
        resp1 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp1.status_code == 200

        # 동일 admin 재승인 시도
        resp2 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        assert detail["error"] == "duplicate_approval"


# ---------------------------------------------------------------------------
# 3. required_approvals >= 2 — 다중 승인
# ---------------------------------------------------------------------------


class TestMultiApprover:
    """required_approvals >= 2: distinct 관리자 N명 승인 도달 시 approved 전환."""

    def test_first_approval_stays_pending(self, client, db_session):
        """policy=2 + admin1 1회 승인 → status='pending', is_approved=False."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp = client.patch(f"/api/v1/skills/{skill.id}/approve")

        assert resp.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value
        assert skill.is_approved is False

    def test_duplicate_after_pending_returns_409(self, client, db_session):
        """pending 상태에서 동일 admin 재승인 시도 → 409 duplicate_approval."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)

        # 1차 승인 (pending 상태로 유지)
        resp1 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp1.status_code == 200

        # 동일 admin 재시도
        resp2 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp2.status_code == 409
        assert resp2.json()["detail"]["error"] == "duplicate_approval"

    def test_second_distinct_admin_triggers_approval(self, client, db_session):
        """policy=2 + admin1 → pending, admin2 → status='approved', approved_by=ADMIN02."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session, category="skill")

        # admin1 1차 승인 (pending)
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp1 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp1.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value

        # admin2 2차 승인 (정족수 충족 → approved)
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        resp2 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp2.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.APPROVED.value
        assert skill.is_approved is True
        assert skill.approved_by == _ADMIN2

    def test_two_distinct_approver_events_recorded(self, client, db_session):
        """policy=2 + admin1, admin2 각각 APPROVE 이벤트 기록 — distinct actor 2명 확인."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        events = (
            db_session.query(SkillGovernanceEvent)
            .filter(
                SkillGovernanceEvent.skill_id == skill.id,
                SkillGovernanceEvent.event_type == SkillGovernanceEventType.APPROVE.value,
            )
            .all()
        )
        assert len(events) == 2
        actors = {e.actor_username for e in events}
        assert actors == {_ADMIN1, _ADMIN2}

    def test_policy_three_requires_three_distinct_admins(self, client, db_session):
        """policy=3 + admin1, admin2 승인해도 여전히 pending — admin3 추가 필요."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 3)
        skill = _insert_skill(db_session, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp1 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp1.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        resp2 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp2.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value
        assert skill.is_approved is False


# ---------------------------------------------------------------------------
# 4. SoD + N-of-M 우선순위
# ---------------------------------------------------------------------------


class TestSoDWithNofM:
    """SoD 위반은 N-of-M 로직보다 우선 처리 — 이벤트 미생성."""

    def test_author_approve_own_skill_blocked_sod(self, client, db_session):
        """policy=2 + author=ADMIN01인 스킬을 ADMIN01이 승인 → 403 sod_violation."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session, author=_ADMIN1, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp = client.patch(f"/api/v1/skills/{skill.id}/approve")

        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "sod_violation"

        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value
        assert skill.is_approved is False

    def test_sod_violation_no_governance_event_created(self, client, db_session):
        """SoD 차단 시 SkillGovernanceEvent 행 생성 없음 (event count=0)."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 2)
        skill = _insert_skill(db_session, author=_ADMIN1, category="skill")

        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp.status_code == 403

        events = (
            db_session.query(SkillGovernanceEvent)
            .filter(SkillGovernanceEvent.skill_id == skill.id)
            .all()
        )
        assert events == []


# ---------------------------------------------------------------------------
# 5. reject는 N-of-M과 무관하게 즉시 처리
# ---------------------------------------------------------------------------


class TestRejectNotAffectedByNofM:
    """reject은 N-of-M 정족수와 무관하게 단일 관리자가 즉시 처리."""

    def test_single_reject_overrides_nofm_pending(self, client, db_session):
        """policy=3 + admin1 approve(pending) + admin2 reject → status='rejected', rejected_by=ADMIN02."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 3)
        skill = _insert_skill(db_session, category="skill")

        # admin1 승인 (pending 상태 — 아직 정족수 미달)
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        resp1 = client.patch(f"/api/v1/skills/{skill.id}/approve")
        assert resp1.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.PENDING.value

        # admin2 반려 — N-of-M 무관하게 즉시 rejected
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        resp2 = client.patch(
            f"/api/v1/skills/{skill.id}/reject",
            params={"reason": "품질 기준 미달"},
        )
        assert resp2.status_code == 200
        db_session.refresh(skill)
        assert skill.approval_status == SkillApprovalStatus.REJECTED.value
        assert skill.is_approved is False
        assert skill.rejected_by == _ADMIN2

    def test_reject_creates_event_independent_of_nofm(self, client, db_session):
        """reject event는 REJECT 타입으로 즉시 기록 — N-of-M pending 상태와 무관."""
        from app.core.security import get_current_user

        _insert_policy(db_session, "skill", 3)
        skill = _insert_skill(db_session, category="skill")

        # admin1 승인 (pending)
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN1)
        client.patch(f"/api/v1/skills/{skill.id}/approve")

        # admin2 반려
        client.app.dependency_overrides[get_current_user] = _mock_admin(_ADMIN2)
        client.patch(
            f"/api/v1/skills/{skill.id}/reject",
            params={"reason": "기준 미달"},
        )

        reject_events = (
            db_session.query(SkillGovernanceEvent)
            .filter(
                SkillGovernanceEvent.skill_id == skill.id,
                SkillGovernanceEvent.event_type == SkillGovernanceEventType.REJECT.value,
            )
            .all()
        )
        assert len(reject_events) == 1
        assert reject_events[0].actor_username == _ADMIN2
