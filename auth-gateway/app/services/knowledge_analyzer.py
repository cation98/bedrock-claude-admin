"""연관 분석, 부서 편차 분석, 갭 분석 서비스."""
import logging
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.knowledge import (
    KnowledgeEdge,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeSnapshot,
    KnowledgeTaxonomy,
    WorkflowTemplate,
)

logger = logging.getLogger(__name__)


def compute_associations(
    db: Session,
    min_support: float = 0.05,
    min_lift: float = 1.5,
) -> list[dict]:
    """Market Basket Analysis 방식 연관 규칙 산출.

    Support  = co_occurrence_count / total_mentions
    Confidence = co_occurrence_count / source_mention_count
    Lift     = co_occurrence_count * total_mentions / (src_count * tgt_count)
    """
    total_mentions: int = db.query(func.count()).select_from(KnowledgeMention).scalar() or 0
    if total_mentions == 0:
        return []

    node_counts: dict[int, int] = dict(
        db.query(KnowledgeMention.node_id, func.count().label("cnt"))
        .group_by(KnowledgeMention.node_id)
        .all()
    )

    results = []
    for edge in db.query(KnowledgeEdge).all():
        co = edge.co_occurrence_count or 0
        if co == 0:
            continue
        src_cnt = node_counts.get(edge.source_node_id, 0)
        tgt_cnt = node_counts.get(edge.target_node_id, 0)
        if src_cnt == 0 or tgt_cnt == 0:
            continue

        support = co / total_mentions
        confidence = co / src_cnt
        lift = (co * total_mentions) / (src_cnt * tgt_cnt)

        if support >= min_support and lift >= min_lift:
            results.append({
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "edge_type": edge.edge_type,
                "support": round(support, 4),
                "confidence": round(confidence, 4),
                "lift": round(lift, 4),
                "co_occurrence_count": co,
            })

    results.sort(key=lambda r: r["lift"], reverse=True)
    return results


def compute_department_stats(db: Session, period: str = "monthly") -> dict:
    """부서별 지식 분포. knowledge_snapshots.department_breakdown 집계."""
    snapshots = (
        db.query(KnowledgeSnapshot)
        .filter(
            KnowledgeSnapshot.granularity == period,
            KnowledgeSnapshot.department_breakdown.isnot(None),
        )
        .all()
    )

    node_dept: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in snapshots:
        breakdown = s.department_breakdown or {}
        if isinstance(breakdown, dict):
            for dept, count in breakdown.items():
                node_dept[s.node_id][dept] += int(count)

    departments = sorted({d for counts in node_dept.values() for d in counts})

    node_lookup: dict[int, KnowledgeNode] = {
        n.id: n
        for n in db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(node_dept.keys())).all()
    }
    rows = []
    for node_id, by_dept in node_dept.items():
        node = node_lookup.get(node_id)
        if not node:
            continue
        rows.append({
            "node_id": node_id,
            "concept_name": node.concept_name,
            "concept_type": node.concept_type,
            "by_department": dict(by_dept),
        })
    rows.sort(key=lambda r: sum(r["by_department"].values()), reverse=True)
    return {"departments": departments, "nodes": rows, "period": period}


def compute_gap_analysis(db: Session, template_id: int) -> dict | None:
    """워크플로우 템플릿 대비 지식 갭 분석."""
    template = db.query(WorkflowTemplate).filter_by(id=template_id).first()
    if not template:
        return None

    active_nodes = db.query(KnowledgeNode).filter_by(is_active=True).all()
    active_ids = {n.id for n in active_nodes}

    taxonomy_entries = (
        db.query(KnowledgeTaxonomy).filter_by(workflow_template_id=template_id).all()
    )
    mapped_ids = {t.knowledge_node_id for t in taxonomy_entries}

    coverage_rate = len(mapped_ids & active_ids) / len(active_ids) if active_ids else 0.0

    mention_counts: dict[int, int] = dict(
        db.query(KnowledgeMention.node_id, func.count().label("cnt"))
        .group_by(KnowledgeMention.node_id)
        .all()
    )

    undocumented = [
        {
            "node_id": node.id,
            "concept_name": node.concept_name,
            "concept_type": node.concept_type,
            "mention_count": mention_counts.get(node.id, 0),
        }
        for node in active_nodes
        if node.id not in mapped_ids and mention_counts.get(node.id, 0) > 0
    ]
    undocumented.sort(key=lambda x: x["mention_count"], reverse=True)

    steps = template.steps or []
    if isinstance(steps, str):
        import json
        steps = json.loads(steps)

    shadow_processes = []
    for step in steps:
        step_id = step.get("id", "")
        step_entries = [t for t in taxonomy_entries if t.workflow_step_id == step_id]
        if not step_entries:
            continue
        total_mentions = sum(mention_counts.get(t.knowledge_node_id, 0) for t in step_entries)
        if total_mentions == 0:
            shadow_processes.append({
                "step_id": step_id,
                "step_name": step.get("name", ""),
                "mapped_nodes": len(step_entries),
                "total_mentions": 0,
            })

    return {
        "template_id": template_id,
        "template_name": template.name,
        "coverage_rate": round(coverage_rate, 4),
        "shadow_processes": shadow_processes,
        "undocumented_knowledge": undocumented[:20],
    }
