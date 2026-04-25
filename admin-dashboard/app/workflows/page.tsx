"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams, useRouter } from "next/navigation";
import {
  fetchWorkflowTemplates,
  fetchWorkflowTemplate,
  createWorkflowTemplate,
  updateWorkflowTemplate,
  deleteWorkflowTemplate,
  type WorkflowTemplateData,
  type WorkflowTemplateIn,
} from "@/lib/api";

const WorkflowCanvas = dynamic(() => import("@/components/WorkflowCanvas"), { ssr: false });

export default function WorkflowsPage() {
  return (
    <Suspense fallback={null}>
      <WorkflowsPageInner />
    </Suspense>
  );
}

function WorkflowsPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const selectedId = searchParams.get("id") ? Number(searchParams.get("id")) : null;

  const [templates, setTemplates] = useState<WorkflowTemplateData[]>([]);
  const [selected, setSelected] = useState<WorkflowTemplateData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      setTemplates(await fetchWorkflowTemplates());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "로드 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  useEffect(() => {
    if (selectedId) {
      fetchWorkflowTemplate(selectedId)
        .then(setSelected)
        .catch(() => setSelected(null));
    } else {
      setSelected(null);
    }
  }, [selectedId]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      const tmpl = await createWorkflowTemplate({
        name: newName.trim(),
        steps: [],
        connections: [],
      });
      setTemplates((prev) => [tmpl, ...prev]);
      setNewName("");
      setCreating(false);
      router.push(`/workflows?id=${tmpl.id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "생성 실패");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("삭제하시겠습니까?")) return;
    await deleteWorkflowTemplate(id);
    setTemplates((prev) => prev.filter((t) => t.id !== id));
    if (selectedId === id) router.push("/workflows");
  };

  const handleSave = async (body: WorkflowTemplateIn) => {
    if (!selected) return;
    const updated = await updateWorkflowTemplate(selected.id, { ...body, name: selected.name });
    setSelected(updated);
  };

  return (
    <div className="flex h-full min-h-screen">
      {/* Sidebar list */}
      <aside className="w-64 shrink-0 border-r border-[var(--border)] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">워크플로우</h2>
          <button
            onClick={() => setCreating(true)}
            className="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white hover:bg-indigo-500"
          >
            + 신규
          </button>
        </div>

        {creating && (
          <div className="mb-3 flex gap-1">
            <input
              autoFocus
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="템플릿 이름"
              className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs text-[var(--text-primary)] outline-none focus:border-indigo-500"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
                if (e.key === "Escape") setCreating(false);
              }}
            />
            <button onClick={handleCreate} className="text-xs text-indigo-400 hover:text-indigo-300">
              OK
            </button>
          </div>
        )}

        {loading && <p className="text-xs text-[var(--text-muted)]">로딩...</p>}
        {error && <p className="text-xs text-red-400">{error}</p>}

        <ul className="space-y-1">
          {templates.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => router.push(`/workflows?id=${t.id}`)}
                className={`group flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm ${
                  selectedId === t.id
                    ? "bg-indigo-600/20 text-indigo-300"
                    : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
                }`}
              >
                <span className="truncate">{t.name}</span>
                <span
                  onClick={(e) => { e.stopPropagation(); handleDelete(t.id); }}
                  className="ml-1 hidden text-xs text-red-400 hover:text-red-300 group-hover:inline"
                >
                  ✕
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      {/* Canvas area */}
      <main className="flex-1 p-6">
        {!selected ? (
          <div className="flex h-64 items-center justify-center text-[var(--text-muted)]">
            <p className="text-sm">왼쪽에서 워크플로우를 선택하거나 신규 생성하세요.</p>
          </div>
        ) : (
          <div>
            <h1 className="mb-4 text-lg font-bold text-[var(--text-primary)]">
              {selected.name}
            </h1>
            <WorkflowCanvas template={selected} onSave={handleSave} />
          </div>
        )}
      </main>
    </div>
  );
}
