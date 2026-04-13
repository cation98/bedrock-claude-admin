"""OnlyOffice 편집 세션 모델 — edit mode + co-editing 추적.

하나의 문서(개인 파일 또는 shared-mount 파일)에 대해 OnlyOffice Document Server가
진행 중인 편집 세션을 추적한다. document_key는 UNIQUE로, 동일 파일에 대한 동시
세션(재진입)을 직렬화한다.

- 개인 파일: is_shared=False, mount_id=NULL, owner_username=파일 소유자
- shared 파일: is_shared=True, mount_id=SharedDataset.id, owner_username=SharedDataset.owner_username

콜백 저장(status=2)이 완료되면 version을 증가시켜 다음 열기 시 새 key가 발급되도록 한다.
OnlyOffice는 한 번 사용된 key의 재편집을 허용하지 않기 때문이다.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.core.database import Base


class EditSession(Base):
    """편집 세션 — 진행 중 / 저장 실패 상태 추적용."""

    __tablename__ = "edit_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # OnlyOffice document key (sha256 앞 20자 + 버전 해시) — UNIQUE
    # 개인:   sha256(f"personal:{username}:{filepath}:{version}")
    # shared: sha256(f"shared:{mount_id}:{filepath}:{version}")
    document_key = Column(String(128), nullable=False, unique=True, index=True)

    # 파일 위치 정보
    file_path = Column(Text, nullable=False)                 # 개인=Pod 내부 경로, shared=dataset 상대 경로
    owner_username = Column(String(50), nullable=False, index=True)
    is_shared = Column(Boolean, nullable=False, default=False)
    mount_id = Column(Integer, nullable=True)                # SharedDataset.id (shared일 때만)

    # 세션을 처음 연 편집자. 같은 사용자가 재진입하면 계속 편집 가능 — 다른 사용자만 view-only.
    # NULL이면(구 데이터) 보수적으로 view-only 처리.
    first_editor_username = Column(String(50), nullable=True, index=True)

    # 상태: editing | saving | saved | save_failed | error
    status = Column(String(20), nullable=False, default="editing", index=True)
    version = Column(Integer, nullable=False, default=1)

    # 마지막 에러 메시지 (status=error|save_failed일 때)
    last_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
