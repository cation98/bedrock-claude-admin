"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { fetchKnowledgeGraph, type KnowledgeGraphData, type KnowledgeNodeData } from "@/lib/api";

const KnowledgeGraph = dynamic(() => import("@/components/KnowledgeGraph"), { ssr: false });

const CONCEPT_TYPES = ["", "skill", "tool", "domain", "method", "problem", "topic"];

export default function KnowledgeGraphPage() {
  const [data, setData] = useState<KnowledgeGraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conceptType, setConceptType] = useState("");
  const [minMentions, setMinMentions] = useState(1);
  const [selected, setSelected] = useState<KnowledgeNodeData | null>(null);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchKnowledgeGraph(conceptType || undefined, minMentions);
      setData(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "알 수 없는 오류");
    } finally {
      setLoading(false);
    }
  }, [conceptType, minMentions]);

  useEffect(() => { loadGraph(); }, [loadGraph]);

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">조직 지식 그래프</h1>
          <p className="text-sm text-[var(--text-muted)]">전 직원 AI 대화에서 자동 추출된 지식 개념 네트워크</p>
        </div>
        <button
          onClick={loadGraph}
          disabled={loading}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {loading ? "로딩 중..." : "새로고침"}
        </button>
      </div>

      <div className="mb-4 flex gap-3 items-center flex-wrap">
        <select
          value={conceptType}
          onChange={(e) => setConceptType(e.target.value)}
          className="rounded border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        >
          {CONCEPT_TYPES.map((t) => (
            <option key={t} value={t}>{t || "전체 유형"}</option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          최소 언급 수:
          <input
            type="number"
            min={1}
            max={100}
            value={minMentions}
            onChange={(e) => setMinMentions(Number(e.target.value))}
            className="w-16 rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-sm text-[var(--text-primary)]"
          />
        </label>
        {data && (
          <span className="text-sm text-[var(--text-muted)]">
            노드 {data.total_nodes}개 · 엣지 {data.total_edges}개
          </span>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
      )}

      {data && data.total_nodes === 0 && !loading && (
        <div className="rounded bg-[var(--surface)] p-8 text-center text-[var(--text-muted)]">
          아직 추출된 지식 개념이 없습니다. 스케줄러가 내일 02:00에 처음 실행됩니다.
        </div>
      )}

      <div className="flex gap-4">
        <div className="flex-1">
          {data && data.total_nodes > 0 && (
            <KnowledgeGraph data={data} onNodeClick={setSelected} />
          )}
        </div>

        {selected && (
          <div className="w-64 rounded border border-[var(--border)] bg-[var(--surface)] p-4 text-sm">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-semibold text-[var(--text-primary)]">노드 상세</span>
              <button onClick={() => setSelected(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">✕</button>
            </div>
            <div className="space-y-1 text-[var(--text-muted)]">
              <div><span className="text-[var(--text-secondary)]">이름:</span> {selected.concept_name}</div>
              <div><span className="text-[var(--text-secondary)]">유형:</span> {selected.concept_type}</div>
              <div><span className="text-[var(--text-secondary)]">언급 수:</span> {selected.mention_count}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
