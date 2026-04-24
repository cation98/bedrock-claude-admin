from pydantic import BaseModel, Field


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


# ── Phase 2: 분석 API 스키마 ──────────────────────────────────────

class AssociationRule(BaseModel):
    source_node_id: int
    target_node_id: int
    edge_type: str
    support: float
    confidence: float
    lift: float
    co_occurrence_count: int


class AssociationsResponse(BaseModel):
    rules: list[AssociationRule]
    total: int


class DepartmentNodeData(BaseModel):
    node_id: int
    concept_name: str
    concept_type: str
    by_department: dict[str, int]


class DepartmentAnalysisResponse(BaseModel):
    departments: list[str]
    nodes: list[DepartmentNodeData]
    period: str


class ShadowProcess(BaseModel):
    step_id: str
    step_name: str
    mapped_nodes: int
    total_mentions: int


class UndocumentedKnowledge(BaseModel):
    node_id: int
    concept_name: str
    concept_type: str
    mention_count: int


class GapReportResponse(BaseModel):
    template_id: int
    template_name: str
    coverage_rate: float
    shadow_processes: list[ShadowProcess]
    undocumented_knowledge: list[UndocumentedKnowledge]


# ── Phase 2: 워크플로우 CRUD 스키마 ──────────────────────────────

class WorkflowTemplateIn(BaseModel):
    name: str
    description: str | None = None
    target_department: str | None = None
    is_public: bool = True
    steps: list[dict] = Field(default_factory=list)
    connections: list[dict] = Field(default_factory=list)


class WorkflowTemplateOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    created_by: str | None = None
    is_public: bool
    target_department: str | None = None
    steps: list[dict] | None = None
    connections: list[dict] | None = None

    model_config = {"from_attributes": True}


# ── Phase 2: taxonomy 스키마 ──────────────────────────────────────

class TaxonomyIn(BaseModel):
    knowledge_node_id: int
    workflow_template_id: int
    workflow_step_id: str
    confidence_score: float | None = None


class TaxonomyOut(BaseModel):
    id: int
    knowledge_node_id: int
    workflow_template_id: int
    workflow_step_id: str
    mapped_by: str | None = None
    confidence_score: float | None = None

    model_config = {"from_attributes": True}
