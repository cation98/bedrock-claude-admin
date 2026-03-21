from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.models import SafetyReport, get_db
from app.schemas import SafetyReportList, SafetyReportResponse

router = APIRouter(prefix="/api/v1/safety", tags=["safety"])


@router.get("/reports", response_model=SafetyReportList)
def list_reports(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    severity: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """안전 점검 보고서 목록 조회"""
    query = db.query(SafetyReport)

    if severity:
        query = query.filter(SafetyReport.severity == severity)
    if status:
        query = query.filter(SafetyReport.status == status)

    total = query.count()
    items = query.offset(skip).limit(limit).all()

    return SafetyReportList(total=total, items=items)


@router.get("/reports/{report_id}", response_model=SafetyReportResponse)
def get_report(report_id: int, db: Session = Depends(get_db)):
    """안전 점검 보고서 상세 조회"""
    report = db.query(SafetyReport).filter(SafetyReport.id == report_id).first()
    if not report:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """안전 점검 통계"""
    total = db.query(SafetyReport).count()
    open_count = db.query(SafetyReport).filter(SafetyReport.status == "open").count()
    resolved_count = db.query(SafetyReport).filter(SafetyReport.is_resolved.is_(True)).count()

    return {
        "total_reports": total,
        "open_reports": open_count,
        "resolved_reports": resolved_count,
        "resolution_rate": f"{(resolved_count / total * 100):.1f}%" if total > 0 else "0%",
    }
