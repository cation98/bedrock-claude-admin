"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchWorkflowTemplates,
  fetchGapReport,
  type WorkflowTemplateData,
  type GapReportData,
} from "@/lib/api";

export default function KnowledgeGapPage() {
  const [templates, setTemplates] = useState<WorkflowTemplateData[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [report, setReport] = useState<GapReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchWorkflowTemplates()
      .then(setTemplates)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "템플릿 로드 실패")
      );
  }, []);

  const loadReport = useCallback(async (templateId: number) => {
    setSelectedId(templateId);
    setLoading(true);
    setError(null);
    try {
      setReport(await fetchGapReport(templateId));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "오류 발생");
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-[var(--text-primary)]">갭 분석</h1>
        <p className="text-sm text-[var(--text-muted)]">
          공식 워크플로우 대비 현장 암묵지 커버리지 분석
        </p>
      </div>

      {/* Template selector */}
      <div className="mb-6 flex flex-wrap gap-2">
        {templates.length === 0 && (
          <p className="text-sm text-[var(--text-muted)]">
            워크플로우 템플릿이 없습니다. /workflows에서 생성하세요.
          </p>
        )}
        {templates.map((t) => (
          <button
            key={t.id}
            onClick={() => loadReport(t.id)}
            className={`rounded px-3 py-1.5 text-sm ${
              selectedId === t.id
                ? "bg-indigo-600 text-white"
                : "bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)]"
            }`}
          >
            {t.name}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
      )}

      {loading && (
        <div className="text-sm text-[var(--text-muted)]">분석 중...</div>
      )}

      {report && !loading && (
        <div className="space-y-6">
          {/* Coverage bar */}
          <div className="rounded border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-[var(--text-primary)]">
                커버리지율 — {report.template_name}
              </span>
              <span className="text-sm font-bold text-indigo-400">
                {(report.coverage_rate * 100).toFixed(1)}%
              </span>
            </div>
            <div className="h-3 w-full overflow-hidden rounded-full bg-[var(--border)]">
              <div
                className="h-full rounded-full bg-indigo-500 transition-all"
                style={{ width: `${report.coverage_rate * 100}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              활성 지식 노드 중 이 워크플로우에 매핑된 비율
            </p>
          </div>

          {/* Shadow processes */}
          <div>
            <h2 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">
              🕳 사문화된 프로세스 ({report.shadow_processes.length}건)
            </h2>
            {report.shadow_processes.length === 0 ? (
              <p className="text-sm text-[var(--text-muted)]">없음</p>
            ) : (
              <div className="overflow-x-auto rounded border border-[var(--border)]">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--border)] bg-[var(--surface)]">
                      <th className="px-4 py-2 text-left text-[var(--text-muted)]">단계</th>
                      <th className="px-4 py-2 text-right text-[var(--text-muted)]">매핑 노드</th>
                      <th className="px-4 py-2 text-right text-[var(--text-muted)]">언급 횟수</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.shadow_processes.map((sp) => (
                      <tr key={sp.step_id}
                          className="border-b border-[var(--border)] hover:bg-[var(--surface-hover)]">
                        <td className="px-4 py-2 text-[var(--text-primary)]">{sp.step_name}</td>
                        <td className="px-4 py-2 text-right text-[var(--text-muted)]">{sp.mapped_nodes}</td>
                        <td className="px-4 py-2 text-right text-red-400">{sp.total_mentions}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Undocumented knowledge */}
          <div>
            <h2 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">
              💡 미문서화 암묵지 top-20 ({report.undocumented_knowledge.length}건)
            </h2>
            {report.undocumented_knowledge.length === 0 ? (
              <p className="text-sm text-[var(--text-muted)]">없음</p>
            ) : (
              <div className="overflow-x-auto rounded border border-[var(--border)]">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--border)] bg-[var(--surface)]">
                      <th className="px-4 py-2 text-left text-[var(--text-muted)]">개념</th>
                      <th className="px-4 py-2 text-left text-[var(--text-muted)]">유형</th>
                      <th className="px-4 py-2 text-right text-[var(--text-muted)]">언급 횟수</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.undocumented_knowledge.map((u) => (
                      <tr key={u.node_id}
                          className="border-b border-[var(--border)] hover:bg-[var(--surface-hover)]">
                        <td className="px-4 py-2 text-[var(--text-primary)]">{u.concept_name}</td>
                        <td className="px-4 py-2 text-[var(--text-muted)]">{u.concept_type}</td>
                        <td className="px-4 py-2 text-right text-emerald-400">{u.mention_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
