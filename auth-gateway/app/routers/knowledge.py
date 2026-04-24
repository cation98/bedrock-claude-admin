"""지식 그래프 API — /api/v1/knowledge/*"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.knowledge import KnowledgeEdge, KnowledgeMention, KnowledgeNode, KnowledgeSnapshot, WorkflowTemplate, KnowledgeTaxonomy
from app.schemas.knowledge import (
    KnowledgeEdgeOut,
    KnowledgeGraphResponse,
    KnowledgeNodeOut,
    KnowledgeTrendNode,
    KnowledgeTrendsResponse,
    AssociationsResponse,
    AssociationRule,
    DepartmentAnalysisResponse,
    DepartmentNodeData,
    GapReportResponse,
    ShadowProcess,
    UndocumentedKnowledge,
)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/graph", response_model=KnowledgeGraphResponse)
def get_knowledge_graph(
    concept_type: str | None = None,
    min_mentions: int = 0,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
) -> KnowledgeGraphResponse:
    """지식 그래프 — 노드와 엣지를 반환."""
    mention_counts = (
        db.query(KnowledgeMention.node_id, func.count().label("cnt"))
        .group_by(KnowledgeMention.node_id)
        .subquery()
    )

    node_query = db.query(KnowledgeNode, mention_counts.c.cnt).outerjoin(
        mention_counts, KnowledgeNode.id == mention_counts.c.node_id
    ).filter(KnowledgeNode.is_active.is_(True))

    if concept_type:
        node_query = node_query.filter(KnowledgeNode.concept_type == concept_type)

    node_rows = node_query.all()
    active_node_ids = set()
    nodes_out: list[KnowledgeNodeOut] = []

    for node, cnt in node_rows:
        count = cnt or 0
        if count >= min_mentions:
            active_node_ids.add(node.id)
            nodes_out.append(
                KnowledgeNodeOut(
                    id=node.id,
                    concept_name=node.concept_name,
                    concept_type=node.concept_type,
                    normalized_name=node.normalized_name,
                    mention_count=count,
                )
            )

    edges_out: list[KnowledgeEdgeOut] = []
    if active_node_ids:
        edges = (
            db.query(KnowledgeEdge)
            .filter(
                KnowledgeEdge.source_node_id.in_(active_node_ids),
                KnowledgeEdge.target_node_id.in_(active_node_ids),
            )
            .all()
        )
        edges_out = [
            KnowledgeEdgeOut(
                id=e.id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                edge_type=e.edge_type,
                weight=e.weight or 1.0,
                co_occurrence_count=e.co_occurrence_count or 0,
            )
            for e in edges
        ]

    return KnowledgeGraphResponse(
        nodes=nodes_out,
        edges=edges_out,
        total_nodes=len(nodes_out),
        total_edges=len(edges_out),
    )


@router.get("/trends", response_model=KnowledgeTrendsResponse)
def get_knowledge_trends(
    weeks: int = 12,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
) -> KnowledgeTrendsResponse:
    """최근 N주간 지식 추이 — 노드별 growth_rate와 주간 언급 수 반환."""
    snapshots = (
        db.query(KnowledgeSnapshot)
        .filter(KnowledgeSnapshot.granularity == "weekly")
        .order_by(KnowledgeSnapshot.snapshot_date.desc())
        .limit(1000)
        .all()
    )

    node_snapshots: dict[int, list] = {}
    for s in snapshots:
        node_snapshots.setdefault(s.node_id, []).append(s)

    trend_nodes: list[KnowledgeTrendNode] = []
    for node_id, snaps in node_snapshots.items():
        snaps_sorted = sorted(snaps, key=lambda x: x.snapshot_date)[-weeks:]
        weekly_counts = [s.mention_count for s in snaps_sorted]

        latest = snaps_sorted[-1] if snaps_sorted else None
        growth_rate = latest.growth_rate if latest else None

        if growth_rate is None:
            trend = "stable"
        elif growth_rate > 0.30:
            trend = "emerging"
        elif growth_rate > 0.15:
            trend = "rising"
        elif growth_rate < -0.20:
            trend = "declining"
        else:
            trend = "stable"

        node = db.query(KnowledgeNode).filter_by(id=node_id).first()
        if not node:
            continue

        trend_nodes.append(
            KnowledgeTrendNode(
                id=node.id,
                concept_name=node.concept_name,
                concept_type=node.concept_type,
                trend=trend,
                growth_rate=growth_rate,
                weekly_counts=weekly_counts,
            )
        )

    trend_nodes.sort(key=lambda n: (n.growth_rate or 0), reverse=True)

    return KnowledgeTrendsResponse(nodes=trend_nodes, period_weeks=weeks)


@router.get("/associations", response_model=AssociationsResponse)
def get_knowledge_associations(
    min_support: float = 0.05,
    min_lift: float = 1.5,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
) -> AssociationsResponse:
    """연관 분석 — MBA Support/Confidence/Lift 규칙 반환."""
    from app.services.knowledge_analyzer import compute_associations
    rules_raw = compute_associations(db, min_support=min_support, min_lift=min_lift)
    rules = [AssociationRule(**r) for r in rules_raw]
    return AssociationsResponse(rules=rules, total=len(rules))


@router.get("/departments", response_model=DepartmentAnalysisResponse)
def get_department_analysis(
    period: str = "monthly",
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
) -> DepartmentAnalysisResponse:
    """부서 편차 분석 — 부서별 지식 분포 반환."""
    from app.services.knowledge_analyzer import compute_department_stats
    result = compute_department_stats(db, period=period)
    nodes = [DepartmentNodeData(**n) for n in result["nodes"]]
    return DepartmentAnalysisResponse(
        departments=result["departments"],
        nodes=nodes,
        period=result["period"],
    )


@router.get("/gap", response_model=GapReportResponse)
def get_gap_analysis(
    template_id: int,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
) -> GapReportResponse:
    """갭 분석 — 워크플로우 템플릿 대비 지식 커버리지 반환."""
    from app.services.knowledge_analyzer import compute_gap_analysis
    result = compute_gap_analysis(db, template_id=template_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return GapReportResponse(
        template_id=result["template_id"],
        template_name=result["template_name"],
        coverage_rate=result["coverage_rate"],
        shadow_processes=[ShadowProcess(**s) for s in result["shadow_processes"]],
        undocumented_knowledge=[UndocumentedKnowledge(**u) for u in result["undocumented_knowledge"]],
    )
