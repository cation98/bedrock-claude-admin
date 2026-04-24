"use client";

import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node as FlowNode,
  type Edge as FlowEdge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { KnowledgeGraphData, KnowledgeNodeData } from "@/lib/api";

const TYPE_COLORS: Record<string, string> = {
  skill: "#6366f1",
  tool: "#10b981",
  domain: "#f59e0b",
  method: "#ec4899",
  problem: "#ef4444",
  topic: "#8b5cf6",
};

function toFlowNodes(nodes: KnowledgeGraphData["nodes"]): FlowNode[] {
  const cols = Math.ceil(Math.sqrt(nodes.length));
  return nodes.map((n, i) => ({
    id: String(n.id),
    position: { x: (i % cols) * 180, y: Math.floor(i / cols) * 120 },
    data: {
      label: n.concept_name,
      type: n.concept_type,
      mentions: n.mention_count,
    },
    style: {
      background: TYPE_COLORS[n.concept_type] ?? "#64748b",
      color: "#fff",
      border: "none",
      borderRadius: "8px",
      fontSize: "11px",
      padding: "6px 10px",
      width: Math.max(80, Math.min(160, n.mention_count * 8 + 60)),
    },
  }));
}

function toFlowEdges(edges: KnowledgeGraphData["edges"]): FlowEdge[] {
  return edges.map((e) => ({
    id: `e${e.id}`,
    source: String(e.source_node_id),
    target: String(e.target_node_id),
    label: e.edge_type,
    style: { strokeWidth: Math.min(e.weight, 4), stroke: "#94a3b8" },
    labelStyle: { fontSize: "9px", fill: "#94a3b8" },
  }));
}

interface Props {
  data: KnowledgeGraphData;
  onNodeClick?: (node: KnowledgeNodeData) => void;
}

export default function KnowledgeGraph({ data, onNodeClick }: Props) {
  const initialNodes = useMemo(() => toFlowNodes(data.nodes), [data.nodes]);
  const initialEdges = useMemo(() => toFlowEdges(data.edges), [data.edges]);
  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: FlowNode) => {
      const original = data.nodes.find((n) => String(n.id) === node.id);
      if (original && onNodeClick) onNodeClick(original);
    },
    [data.nodes, onNodeClick]
  );

  return (
    <div style={{ width: "100%", height: "600px", background: "#0f172a", borderRadius: "8px" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        fitView
        colorMode="dark"
      >
        <Background color="#1e293b" />
        <Controls />
        <MiniMap nodeColor={(n) => TYPE_COLORS[(n.data?.type as string) ?? ""] ?? "#64748b"} />
      </ReactFlow>
    </div>
  );
}
