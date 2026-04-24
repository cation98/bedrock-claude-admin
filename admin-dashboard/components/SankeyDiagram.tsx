"use client";

import type { KnowledgeTrendsData } from "@/lib/api";

const TREND_COLORS: Record<string, string> = {
  emerging: "#10b981",
  rising: "#6366f1",
  stable: "#64748b",
  declining: "#ef4444",
};

const TYPE_COLORS: Record<string, string> = {
  skill: "#6366f1",
  tool: "#10b981",
  domain: "#f59e0b",
  method: "#8b5cf6",
  problem: "#ef4444",
  topic: "#64748b",
};

interface SankeyNodeLayout {
  id: string;
  name: string;
  x: number;
  y: number;
  height: number;
  color: string;
}

interface SankeyLinkLayout {
  sourceId: string;
  targetId: string;
  value: number;
  color: string;
}

function buildSankeyLayout(data: KnowledgeTrendsData): {
  nodes: SankeyNodeLayout[];
  links: SankeyLinkLayout[];
  svgWidth: number;
  svgHeight: number;
} {
  const typeTrendCounts: Record<string, Record<string, number>> = {};

  for (const node of data.nodes) {
    if (!typeTrendCounts[node.concept_type]) typeTrendCounts[node.concept_type] = {};
    const t = typeTrendCounts[node.concept_type];
    t[node.trend] = (t[node.trend] || 0) + 1;
  }

  const conceptTypes = Object.keys(typeTrendCounts);
  const trendStates = ["emerging", "rising", "stable", "declining"].filter(
    (t) => data.nodes.some((n) => n.trend === t)
  );

  const svgWidth = 560;
  const nodeWidth = 120;
  const gap = 6;
  const totalValue = data.nodes.length || 1;
  const svgHeight = Math.max(200, totalValue * 16 + (conceptTypes.length + trendStates.length) * gap);

  const leftNodes: SankeyNodeLayout[] = [];
  let y = 20;
  for (const ct of conceptTypes) {
    const val = Object.values(typeTrendCounts[ct]).reduce((a, b) => a + b, 0);
    const h = Math.max(20, (val / totalValue) * (svgHeight - 40));
    leftNodes.push({
      id: `type_${ct}`,
      name: ct,
      x: 20,
      y,
      height: h,
      color: TYPE_COLORS[ct] || "#64748b",
    });
    y += h + gap;
  }

  const rightNodes: SankeyNodeLayout[] = [];
  y = 20;
  for (const ts of trendStates) {
    const val = data.nodes.filter((n) => n.trend === ts).length;
    const h = Math.max(20, (val / totalValue) * (svgHeight - 40));
    rightNodes.push({
      id: `trend_${ts}`,
      name: ts,
      x: svgWidth - nodeWidth - 20,
      y,
      height: h,
      color: TREND_COLORS[ts] || "#64748b",
    });
    y += h + gap;
  }

  const nodeMap = Object.fromEntries(
    [...leftNodes, ...rightNodes].map((n) => [n.id, n])
  );

  const links: SankeyLinkLayout[] = [];
  const leftOffsets: Record<string, number> = {};
  const rightOffsets: Record<string, number> = {};

  for (const ct of conceptTypes) {
    for (const ts of trendStates) {
      const count = typeTrendCounts[ct]?.[ts] || 0;
      if (count === 0) continue;
      const srcNode = nodeMap[`type_${ct}`];
      const tgtNode = nodeMap[`trend_${ts}`];
      if (!srcNode || !tgtNode) continue;

      const linkH = Math.max(2, (count / totalValue) * (srcNode.height - 4));

      links.push({
        sourceId: `type_${ct}`,
        targetId: `trend_${ts}`,
        value: count,
        color: srcNode.color,
      });

      leftOffsets[`type_${ct}`] = (leftOffsets[`type_${ct}`] || 2) + linkH + 1;
      rightOffsets[`trend_${ts}`] = (rightOffsets[`trend_${ts}`] || 2) + linkH + 1;
    }
  }

  return {
    nodes: [...leftNodes, ...rightNodes],
    links,
    svgWidth,
    svgHeight,
  };
}

export default function SankeyDiagram({ data }: { data: KnowledgeTrendsData }) {
  if (data.nodes.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
        스냅샷 데이터가 없습니다.
      </div>
    );
  }

  const { nodes, svgWidth, svgHeight } = buildSankeyLayout(data);
  const nodeWidth = 120;

  return (
    <div className="overflow-x-auto">
      <svg width={svgWidth} height={svgHeight} className="min-w-[400px]">
        {nodes.map((node) => (
          <g key={node.id}>
            <rect
              x={node.x}
              y={node.y}
              width={nodeWidth}
              height={node.height}
              fill={node.color}
              opacity={0.85}
              rx={3}
            />
            <text
              x={node.x + nodeWidth / 2}
              y={node.y + node.height / 2}
              textAnchor="middle"
              dominantBaseline="middle"
              fill="white"
              fontSize={11}
              fontWeight={500}
            >
              {node.name}
            </text>
          </g>
        ))}
      </svg>
      <p className="mt-2 text-xs text-[var(--text-muted)]">
        좌: 개념 유형 → 우: 추이 상태 (너비 = 노드 수 기준)
      </p>
    </div>
  );
}
