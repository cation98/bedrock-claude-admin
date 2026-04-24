from pydantic import BaseModel


class KnowledgeNodeOut(BaseModel):
    id: int
    concept_name: str
    concept_type: str
    normalized_name: str
    mention_count: int = 0

    model_config = {"from_attributes": True}


class KnowledgeEdgeOut(BaseModel):
    id: int
    source_node_id: int
    target_node_id: int
    edge_type: str
    weight: float
    co_occurrence_count: int

    model_config = {"from_attributes": True}


class KnowledgeGraphResponse(BaseModel):
    nodes: list[KnowledgeNodeOut]
    edges: list[KnowledgeEdgeOut]
    total_nodes: int
    total_edges: int


class KnowledgeTrendNode(BaseModel):
    id: int
    concept_name: str
    concept_type: str
    trend: str           # emerging | rising | stable | declining
    growth_rate: float | None
    weekly_counts: list[int]  # 최근 12주


class KnowledgeTrendsResponse(BaseModel):
    nodes: list[KnowledgeTrendNode]
    period_weeks: int
