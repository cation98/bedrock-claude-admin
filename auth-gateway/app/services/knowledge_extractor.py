"""암묵지 추출 서비스 — Claude Haiku 4.5 via AWS Bedrock."""
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeEdge, KnowledgeMention, KnowledgeNode
from app.models.prompt_audit import PromptAuditConversation

logger = logging.getLogger(__name__)

HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
BATCH_SIZE = 8
MAX_CONVERSATIONS = 500

_SYSTEM_PROMPT = (
    "다음 AI 대화 내용에서 사용자가 다루는 지식 개념, 기술, 도구, 방법론을 추출하라. "
    "반드시 아래 JSON 형식으로만 응답하고 다른 텍스트는 포함하지 않는다.\n"
    '{"concepts": [{"name": "개념명", "type": "skill|tool|domain|method|problem|topic", "confidence": 0.0~1.0}], '
    '"relationships": [{"source": "개념A", "target": "개념B", "type": "co_occurs|precedes|enables|relates_to"}]}'
)


def normalize_name(name: str) -> str:
    """개념명을 소문자+공백 정규화 (dedup 키로 사용)."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_extraction_response(raw: str) -> dict:
    """Bedrock 응답 JSON 파싱. 실패 시 빈 dict 반환."""
    try:
        data = json.loads(raw)
        if "concepts" not in data:
            return {"concepts": [], "relationships": []}
        if "relationships" not in data:
            data["relationships"] = []
        return data
    except (json.JSONDecodeError, TypeError):
        return {"concepts": [], "relationships": []}


def group_conversations_by_session(conversations: list) -> dict:
    """conversation 목록을 session_id별로 그룹핑."""
    groups: dict[str, list] = defaultdict(list)
    for conv in conversations:
        key = conv.session_id or f"no-session-{conv.id}"
        groups[key].append(conv)
    return dict(groups)


def _build_prompt(sessions: dict[str, list]) -> str:
    """세션 그룹을 하나의 프롬프트 텍스트로 합산."""
    lines = []
    for session_id, convs in sessions.items():
        lines.append(f"[세션 {session_id}]")
        for c in convs:
            role = "사용자" if c.message_type == "user" else "AI"
            snippet = (c.content or "")[:300]
            lines.append(f"{role}: {snippet}")
    return "\n".join(lines)


def _call_haiku(prompt_text: str, client) -> str:
    """AWS Bedrock converse API로 Claude Haiku 호출."""
    response = client.converse(
        modelId=HAIKU_MODEL_ID,
        system=[{"text": _SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt_text}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
    )
    return response["output"]["message"]["content"][0]["text"]


_ALLOWED_CONCEPT_TYPES = {"skill", "tool", "domain", "method", "problem", "topic"}


def _upsert_node(db: Session, concept: dict, now: datetime) -> KnowledgeNode | None:
    """개념을 knowledge_nodes에 upsert. normalized_name 기준 중복 병합."""
    name = (concept.get("name") or "").strip()
    ctype = concept.get("type", "topic")
    if ctype not in _ALLOWED_CONCEPT_TYPES:
        ctype = "topic"
    if not name:
        return None
    normalized = normalize_name(name)
    node = db.query(KnowledgeNode).filter_by(normalized_name=normalized).first()
    if node is None:
        node = KnowledgeNode(
            concept_name=name,
            concept_type=ctype,
            normalized_name=normalized,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(node)
        db.flush()
    else:
        node.last_seen_at = now
    return node


def _upsert_edge(
    db: Session, source_id: int, target_id: int, edge_type: str, now: datetime
) -> None:
    """엣지 upsert — 이미 있으면 co_occurrence_count 증가."""
    edge = (
        db.query(KnowledgeEdge)
        .filter_by(source_node_id=source_id, target_node_id=target_id, edge_type=edge_type)
        .first()
    )
    if edge is None:
        edge = KnowledgeEdge(
            source_node_id=source_id,
            target_node_id=target_id,
            edge_type=edge_type,
            co_occurrence_count=1,
            last_seen_at=now,
        )
        db.add(edge)
    else:
        edge.co_occurrence_count = (edge.co_occurrence_count or 0) + 1
        edge.weight = min(float(edge.co_occurrence_count) / 10.0, 5.0)
        edge.last_seen_at = now


def run_extraction(db: Session, region: str = "us-east-1") -> int:
    """미처리 대화를 배치 처리하여 knowledge 테이블에 저장. 처리한 대화 수 반환."""
    conversations = (
        db.query(PromptAuditConversation)
        .filter(PromptAuditConversation.knowledge_extracted_at.is_(None))
        .order_by(PromptAuditConversation.timestamp.asc())
        .limit(MAX_CONVERSATIONS)
        .all()
    )
    if not conversations:
        logger.info("knowledge extraction: no unprocessed conversations")
        return 0

    session_groups = group_conversations_by_session(conversations)
    session_keys = list(session_groups.keys())
    now = datetime.now(timezone.utc)
    processed_conv_ids: list[int] = []
    client = boto3.client("bedrock-runtime", region_name=region)

    for batch_start in range(0, len(session_keys), BATCH_SIZE):
        batch_keys = session_keys[batch_start: batch_start + BATCH_SIZE]
        batch = {k: session_groups[k] for k in batch_keys}
        prompt_text = _build_prompt(batch)

        raw = "{}"
        for attempt in range(3):
            try:
                raw = _call_haiku(prompt_text, client)
                break
            except Exception as exc:
                if attempt == 2:
                    logger.error(f"knowledge extraction batch failed after 3 tries: {exc}")
                    break
                logger.warning(f"knowledge extraction attempt {attempt+1} failed: {exc}")

        extracted = parse_extraction_response(raw)

        node_map: dict[str, KnowledgeNode] = {}
        for concept in extracted.get("concepts", []):
            node = _upsert_node(db, concept, now)
            if node:
                node_map[normalize_name(concept["name"])] = node

        for rel in extracted.get("relationships", []):
            src_key = normalize_name(rel.get("source", ""))
            tgt_key = normalize_name(rel.get("target", ""))
            edge_type = rel.get("type", "co_occurs")
            if src_key in node_map and tgt_key in node_map:
                _upsert_edge(db, node_map[src_key].id, node_map[tgt_key].id, edge_type, now)

        for _, convs in batch.items():
            for conv in convs:
                for concept in extracted.get("concepts", []):
                    normalized = normalize_name(concept.get("name", ""))
                    node = node_map.get(normalized)
                    if node:
                        snippet = (conv.content or "")[:30]
                        mention = KnowledgeMention(
                            conversation_id=conv.id,
                            node_id=node.id,
                            username=conv.username,
                            session_id=conv.session_id,
                            context_snippet=snippet,
                            confidence_score=concept.get("confidence"),
                            mentioned_at=conv.timestamp,
                        )
                        db.add(mention)
                processed_conv_ids.append(conv.id)

        db.flush()

    if processed_conv_ids:
        db.query(PromptAuditConversation).filter(
            PromptAuditConversation.id.in_(processed_conv_ids)
        ).update({"knowledge_extracted_at": now}, synchronize_session=False)

    db.commit()
    logger.info(f"knowledge extraction complete: {len(processed_conv_ids)} conversations")
    return len(processed_conv_ids)
