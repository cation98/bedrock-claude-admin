"""현장 데이터 수집 양식 API 라우터.

Endpoints:
  POST   /api/v1/surveys                -- 양식 생성
  GET    /api/v1/surveys                -- 양식 목록 (response_count 포함)
  GET    /api/v1/surveys/photo-url      -- S3 사진 pre-signed URL 생성
  GET    /api/v1/surveys/{id}           -- 양식 상세 조회
  POST   /api/v1/surveys/{id}/assign    -- 양식 배포 (사용자 할당)
  GET    /api/v1/surveys/{id}/responses -- 양식 응답 목록 조회
"""

import logging

import boto3
from botocore.exceptions import NoCredentialsError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.survey import SurveyAssignment, SurveyResponse, SurveyTemplate
from app.routers.telegram import TelegramMapping
from app.schemas.survey import (
    AssignSurveyRequest,
    CreateSurveyRequest,
    SurveyAssignmentResponse,
    SurveyResponseItem,
    SurveyTemplateResponse,
)

router = APIRouter(prefix="/api/v1/surveys", tags=["surveys"])
logger = logging.getLogger(__name__)

S3_BUCKET = "bedrock-claude-surveys"


# ==================== 고정 경로 엔드포인트 (동적 경로보다 먼저 선언) ====================


@router.get("/photo-url")
async def get_photo_presigned_url(
    s3_key: str = Query(..., description="S3 object key"),
    current_user: dict = Depends(get_current_user),
):
    """S3 사진 pre-signed URL 생성 (읽기 전용, 1시간 유효)."""
    if not s3_key or not s3_key.strip():
        raise HTTPException(status_code=400, detail="s3_key는 필수입니다")

    try:
        s3_client = boto3.client("s3")
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=3600,
        )
    except NoCredentialsError:
        raise HTTPException(
            status_code=500,
            detail="S3 자격 증명이 설정되지 않았습니다",
        )
    except Exception as e:
        logger.error("S3 pre-signed URL 생성 실패: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="S3 URL 생성에 실패했습니다")

    return {"url": url, "s3_key": s3_key, "expires_in": 3600}


# ==================== 양식 CRUD ====================


@router.post("", response_model=SurveyTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_survey(
    request: CreateSurveyRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """양식 생성. 질문은 1~20개, type은 text/photo/choice만 허용."""
    username = current_user["sub"]

    template = SurveyTemplate(
        owner_username=username,
        title=request.title,
        description=request.description,
        questions=[q.model_dump() for q in request.questions],
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    resp = SurveyTemplateResponse.model_validate(template)
    resp.response_count = 0
    logger.info("Survey template created: id=%s by %s", template.id, username)
    return resp


@router.get("", response_model=list[SurveyTemplateResponse])
async def list_surveys(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """양식 목록 조회 (각 양식의 완료된 응답 수 포함)."""
    # response_count 서브쿼리: assignment별 unique response를 집계
    response_count_sq = (
        db.query(
            SurveyAssignment.template_id,
            func.count(SurveyResponse.id).label("response_count"),
        )
        .join(SurveyResponse, SurveyResponse.assignment_id == SurveyAssignment.id)
        .group_by(SurveyAssignment.template_id)
        .subquery()
    )

    rows = (
        db.query(SurveyTemplate, func.coalesce(response_count_sq.c.response_count, 0))
        .outerjoin(response_count_sq, SurveyTemplate.id == response_count_sq.c.template_id)
        .order_by(SurveyTemplate.created_at.desc())
        .all()
    )

    results = []
    for template, count in rows:
        resp = SurveyTemplateResponse.model_validate(template)
        resp.response_count = count
        results.append(resp)

    return results


# ==================== 동적 경로 엔드포인트 ====================


@router.get("/{survey_id}", response_model=SurveyTemplateResponse)
async def get_survey(
    survey_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """양식 상세 조회."""
    template = db.query(SurveyTemplate).filter(SurveyTemplate.id == survey_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="양식을 찾을 수 없습니다")

    # response_count 계산
    count = (
        db.query(func.count(SurveyResponse.id))
        .join(SurveyAssignment, SurveyResponse.assignment_id == SurveyAssignment.id)
        .filter(SurveyAssignment.template_id == survey_id)
        .scalar()
    ) or 0

    resp = SurveyTemplateResponse.model_validate(template)
    resp.response_count = count
    return resp


@router.post("/{survey_id}/assign", response_model=list[SurveyAssignmentResponse], status_code=status.HTTP_201_CREATED)
async def assign_survey(
    survey_id: int,
    request: AssignSurveyRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """양식을 사용자에게 배포(할당). TelegramMapping에서 telegram_id 조회."""
    template = db.query(SurveyTemplate).filter(SurveyTemplate.id == survey_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="양식을 찾을 수 없습니다")

    # 대상 사용자별 telegram_id 일괄 조회 (N+1 방지)
    target_usernames_upper = [u.upper() for u in request.target_usernames]
    mappings = (
        db.query(TelegramMapping)
        .filter(TelegramMapping.username.in_(target_usernames_upper))
        .all()
    )
    tg_map = {m.username: str(m.telegram_id) for m in mappings}

    assignments = []
    for uname in target_usernames_upper:
        assignment = SurveyAssignment(
            template_id=survey_id,
            target_username=uname,
            telegram_id=tg_map.get(uname),
        )
        db.add(assignment)
        assignments.append(assignment)

    db.commit()
    for a in assignments:
        db.refresh(a)

    logger.info(
        "Survey id=%s assigned to %d users by %s",
        survey_id, len(assignments), current_user["sub"],
    )
    return [SurveyAssignmentResponse.model_validate(a) for a in assignments]


@router.get("/{survey_id}/responses", response_model=list[SurveyResponseItem])
async def get_survey_responses(
    survey_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """양식에 대한 모든 완료된 응답 조회."""
    template = db.query(SurveyTemplate).filter(SurveyTemplate.id == survey_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="양식을 찾을 수 없습니다")

    # template에 연결된 assignment들의 response 조회
    responses = (
        db.query(SurveyResponse)
        .join(SurveyAssignment, SurveyResponse.assignment_id == SurveyAssignment.id)
        .filter(SurveyAssignment.template_id == survey_id)
        .order_by(SurveyResponse.completed_at.desc())
        .all()
    )

    return [SurveyResponseItem.model_validate(r) for r in responses]
