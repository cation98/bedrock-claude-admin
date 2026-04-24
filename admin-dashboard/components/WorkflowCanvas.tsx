"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  addEdge,
  useNodesState,
  useEdgesState,
  type Node as FlowNode,
  type Edge as FlowEdge,
  type Connection,
} from "@xyflow/react";
// @ts-expect-error — no type declarations for CSS side-effect import
import "@xyflow/react/dist/style.css";
import type { WorkflowTemplateData, WorkflowTemplateIn } from "@/lib/api";

interface Props {
  template: WorkflowTemplateData;
  onSave: (body: WorkflowTemplateIn) => Promise<void>;
}

function stepsToNodes(steps: WorkflowTemplateData["steps"]): FlowNode[] {
  return (steps || []).map((s, i) => ({
    id: s.id,
    data: { label: s.name },
    position: { x: i * 220, y: 100 },
  }));
}

function connectionsToEdges(connections: WorkflowTemplateData["connections"]): FlowEdge[] {
  return (connections || []).map((c, i) => ({
    id: `e-${i}`,
    source: c.from,
    target: c.to,
    label: c.label || undefined,
  }));
}

export default function WorkflowCanvas({ template, onSave }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState(stepsToNodes(template.steps));
  const [edges, setEdges, onEdgesChange] = useEdgesState(connectionsToEdges(template.connections));
  const [saving, setSaving] = useState(false);
  const [newStepName, setNewStepName] = useState("");

  useEffect(() => {
    setNodes(stepsToNodes(template.steps));
    setEdges(connectionsToEdges(template.connections));
  }, [template.id, setNodes, setEdges]);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  );

  const addStep = () => {
    const name = newStepName.trim() || `단계 ${nodes.length + 1}`;
    const id = `s${Date.now()}`;
    setNodes((nds) => [
      ...nds,
      { id, data: { label: name }, position: { x: nds.length * 220, y: 100 } },
    ]);
    setNewStepName("");
  };

  const handleSave = async () => {
    setSaving(true);
    const steps = nodes.map((n) => ({ id: n.id, name: String(n.data.label) }));
    const connections = edges.map((e) => ({
      from: e.source,
      to: e.target,
      label: String(e.label || ""),
    }));
    await onSave({ name: template.name, description: template.description ?? undefined, steps, connections });
    setSaving(false);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <input
          type="text"
          value={newStepName}
          onChange={(e) => setNewStepName(e.target.value)}
          placeholder="새 단계 이름"
          className="rounded border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm text-[var(--text-primary)] outline-none focus:border-indigo-500"
          onKeyDown={(e) => e.key === "Enter" && addStep()}
        />
        <button
          onClick={addStep}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500"
        >
          + 단계 추가
        </button>
        <button
          onClick={handleSave}
          disabled={saving}
          className="ml-auto rounded bg-emerald-600 px-4 py-1.5 text-sm text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {saving ? "저장 중..." : "저장"}
        </button>
      </div>
      <div style={{ height: 400 }} className="rounded border border-[var(--border)]">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        노드를 드래그해 배치하고, 포트를 연결해 흐름을 정의하세요. 저장 후 taxonomy 매핑 가능.
      </p>
    </div>
  );
}
