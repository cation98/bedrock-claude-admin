"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import {
  fetchKnowledgeTrends,
  fetchKnowledgeAssociations,
  type KnowledgeTrendsData,
  type AssociationsData,
} from "@/lib/api";

const SankeyDiagram = dynamic(() => import("@/components/SankeyDiagram"), { ssr: false });

const TREND_COLORS: Record<string, string> = {
  emerging: "#10b981",
  rising: "#6366f1",
  stable: "#64748b",
  declining: "#ef4444",
};

const TREND_LABELS: Record<string, string> = {
  emerging: "🚀 Emerging",
  rising: "📈 Rising",
  stable: "➡ Stable",
  declining: "📉 Declining",
};

function Sparkline({ counts }: { counts: number[] }) {
  if (counts.length === 0) return null;
  const max = Math.max(...counts, 1);
  const w = 80;
  const h = 24;
  const pts = counts
    .map((v, i) => `${(i / (counts.length - 1)) * w},${h - (v / max) * h}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="opacity-70">
      <polyline points={pts} fill="none" stroke="#6366f1" strokeWidth="1.5" />
    </svg>
  );
}

export default function KnowledgeTrendsPage() {
  const [data, setData] = useState<KnowledgeTrendsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [activeTab, setActiveTab] = useState<"trends" | "sankey" | "associations">("trends");
  const [associations, setAssociations] = useState<AssociationsData | null>(null);
  const [assocLoading, setAssocLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchKnowledgeTrends(12));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "오류 발생");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = data
    ? filter === "all"
      ? data.nodes
      : data.nodes.filter((n) => n.trend === filter)
    : [];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">지식 추이 분석</h1>
          <p className="text-sm text-[var(--text-muted)]">최근 12주간 개념별 언급 추이</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {loading ? "로딩 중..." : "새로고침"}
        </button>
      </div>

      {/* 탭 선택 */}
      <div className="mb-4 flex gap-2 border-b border-[var(--border)] pb-2">
        {(["trends", "sankey", "associations"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => {
              setActiveTab(tab);
              if (tab === "associations" && !associations && !assocLoading) {
                setAssocLoading(true);
                fetchKnowledgeAssociations(0.0, 1.0)
                  .then(setAssociations)
                  .finally(() => setAssocLoading(false));
              }
            }}
            className={`rounded-t px-4 py-1.5 text-sm ${
              activeTab === tab
                ? "bg-indigo-600 text-white"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {tab === "trends" ? "추이 목록" : tab === "sankey" ? "Sankey 흐름" : "연관 규칙"}
          </button>
        ))}
      </div>

      {activeTab === "trends" && (
        <>
          <div className="mb-4 flex gap-2">
            {["all", "emerging", "rising", "stable", "declining"].map((t) => (
              <button
                key={t}
                onClick={() => setFilter(t)}
                className={`rounded px-3 py-1 text-sm ${
                  filter === t
                    ? "bg-indigo-600 text-white"
                    : "bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)]"
                }`}
              >
                {t === "all" ? "전체" : TREND_LABELS[t]}
              </button>
            ))}
          </div>

          {error && (
            <div className="mb-4 rounded bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
          )}

          {filtered.length === 0 && !loading && (
            <div className="rounded bg-[var(--surface)] p-8 text-center text-[var(--text-muted)]">
              {data ? "해당 추이 데이터가 없습니다." : "스냅샷 데이터가 아직 없습니다. 첫 실행 후 확인하세요."}
            </div>
          )}

          {filtered.length > 0 && (
            <div className="overflow-x-auto rounded border border-[var(--border)]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--border)] bg-[var(--surface)]">
                    <th className="px-4 py-2 text-left text-[var(--text-muted)]">개념</th>
                    <th className="px-4 py-2 text-left text-[var(--text-muted)]">유형</th>
                    <th className="px-4 py-2 text-left text-[var(--text-muted)]">추이</th>
                    <th className="px-4 py-2 text-right text-[var(--text-muted)]">성장률</th>
                    <th className="px-4 py-2 text-left text-[var(--text-muted)]">12주 스파크라인</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((node) => (
                    <tr
                      key={node.id}
                      className="border-b border-[var(--border)] hover:bg-[var(--surface-hover)]"
                    >
                      <td className="px-4 py-2 text-[var(--text-primary)]">{node.concept_name}</td>
                      <td className="px-4 py-2 text-[var(--text-muted)]">{node.concept_type}</td>
                      <td className="px-4 py-2">
                        <span style={{ color: TREND_COLORS[node.trend] }}>{TREND_LABELS[node.trend]}</span>
                      </td>
                      <td className="px-4 py-2 text-right" style={{ color: TREND_COLORS[node.trend] }}>
                        {node.growth_rate != null
                          ? `${node.growth_rate > 0 ? "+" : ""}${(node.growth_rate * 100).toFixed(1)}%`
                          : "—"}
                      </td>
                      <td className="px-4 py-2">
                        <Sparkline counts={node.weekly_counts} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {activeTab === "sankey" && data && (
        <div className="rounded border border-[var(--border)] bg-[var(--surface)] p-4">
          <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">
            개념 유형 → 추이 상태 흐름
          </h2>
          <SankeyDiagram data={data} />
        </div>
      )}

      {activeTab === "associations" && (
        <div>
          {assocLoading && <p className="text-sm text-[var(--text-muted)]">로딩 중...</p>}
          {associations && (
            <div className="overflow-x-auto rounded border border-[var(--border)]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--border)] bg-[var(--surface)]">
                    <th className="px-4 py-2 text-left text-[var(--text-muted)]">Source ID</th>
                    <th className="px-4 py-2 text-left text-[var(--text-muted)]">Target ID</th>
                    <th className="px-4 py-2 text-right text-[var(--text-muted)]">Support</th>
                    <th className="px-4 py-2 text-right text-[var(--text-muted)]">Confidence</th>
                    <th className="px-4 py-2 text-right text-[var(--text-muted)]">Lift</th>
                  </tr>
                </thead>
                <tbody>
                  {associations.rules.map((r, i) => (
                    <tr
                      key={i}
                      className="border-b border-[var(--border)] hover:bg-[var(--surface-hover)]"
                    >
                      <td className="px-4 py-2 text-[var(--text-muted)]">{r.source_node_id}</td>
                      <td className="px-4 py-2 text-[var(--text-muted)]">{r.target_node_id}</td>
                      <td className="px-4 py-2 text-right">{(r.support * 100).toFixed(1)}%</td>
                      <td className="px-4 py-2 text-right">{(r.confidence * 100).toFixed(1)}%</td>
                      <td className="px-4 py-2 text-right text-emerald-400">{r.lift.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {associations.rules.length === 0 && (
                <p className="p-4 text-center text-sm text-[var(--text-muted)]">
                  연관 규칙이 없습니다 (데이터 부족).
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
