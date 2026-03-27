"""감사 로그 기록 서비스."""
import logging

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def log_audit(db, actor: str, action: str, target: str = None,
              detail: str = None, ip: str = None, metadata: dict = None):
    """감사 로그 1건 기록. commit은 호출자가 담당."""
    entry = AuditLog(
        actor=actor,
        action=action,
        target=target,
        detail=detail,
        ip_address=ip,
        metadata_=metadata,
    )
    db.add(entry)
    logger.info(f"AUDIT: {actor} {action} {target or ''} {detail or ''}")
