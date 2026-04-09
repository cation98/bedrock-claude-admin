"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getPromptAuditSummary,
  getPromptAuditFlags,
  reviewPromptFlag,
  triggerPromptAudit,
  type PromptAuditSummaryResponse,
  type PromptAuditFlag,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import StatsCard from "@/components/stats-card";

const REFRESH_INTERVAL = 60_000;

type Tab = "usage" | "security";

const CATEGORY_LABELS: Record<string, string> = {
  data_analysis: "데이터 분석",
  coding: "코딩/개발",
  database: "DB 조회",
  reporting: "보고서",
  webapp: "웹앱",
  infra: "인프라",
  documentation: "문서/설명",
  safety_mgmt: "안전관리",
  facility: "시설정보",
  quality: "품질",
  business_analysis: "업무분석",
  scm: "SCM/구매",
  communication: "커뮤니케이션",
  other: "기타",
};

const CATEGORY_COLORS: Record<string, string> = {
  data_analysis: "#3b82f6",
  coding: "#10b981",
  database: "#8b5cf6",
  reporting: "#f59e0b",
  webapp: "#ec4899",
  infra: "#6366f1",
  documentation: "#14b8a6",
  safety_mgmt: "#ef4444",
  facility: "#f97316",
  quality: "#06b6d4",
  business_analysis: "#a855f7",
  scm: "#84cc16",
  communication: "#0ea5e9",
  other: "#6b7280",
};

function CategoryBarChart({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    return <div className="py-8 text-center text-sm text-[var(--text-muted)]">카테고리 데이터가 없습니다</div>;
  }
  const max = Math.max(...entries.map((e) => e[1])) || 1;
  const barHeight = 28;
  const labelWidth = 100;
  const chartWidth = 400;
  const height = entries.length * (barHeight + 8) + 20;

  return (
    <svg width={labelWidth + chartWidth + 60} height={height}>
      {entries.map(([cat, count], i) => {
        const y = i * (barHeight + 8) + 10;
        const w = (count / max) * chartWidth;
        const color = CATEGORY_COLORS[cat] || "#6b7280";
        return (
          <g key={cat}>
            <text
              x={labelWidth - 8}
              y={y + barHeight / 2 + 4}
              textAnchor="end"
              fontSize="12"
              fill="#6b7280"
            >
              {CATEGORY_LABELS[cat] || cat}
            </text>
            <rect
              x={labelWidth}
              y={y}
              width={w}
              height={barHeight}
              rx={4}
              fill={color}
              opacity={0.8}
            />
            <text
              x={labelWidth + w + 6}
              y={y + barHeight / 2 + 4}
              fontSize="11"
              fill="#9ca3af"
            >
              {count.toLocaleString()}건
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function severityBadge(severity: string) {
  const base =
    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (severity) {
    case "critical":
      return <span className={`${base} bg-[var(--danger-light)] text-[var(--danger)]`}>CRITICAL</span>;
    case "high":
      return <span className={`${base} bg-[var(--error-light)] text-[var(--error)]`}>HIGH</span>;
    case "medium":
      return <span className={`${base} bg-[var(--warning-light)] text-[var(--warning)]`}>MEDIUM</span>;
    case "low":
      return <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>LOW</span>;
    default:
      return <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>{severity}</span>;
  }
}

function topCategories(counts: Record<string, number>): { cat: string; count: number }[] {
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([cat, count]) => ({ cat, count }));
}

function formatDateTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function AuditPage() {
  const router = useRouter();

  const [tab, setTab] = useState<Tab>("usage");

  // Date range
  const [dateFrom, setDateFrom] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [dateTo, setDateTo] = useState(() => new Date().toISOString().slice(0, 10));

  // Usage tab data
  const [summary, setSummary] = useState<PromptAuditSummaryResponse | null>(null);

  // Security tab data
  const [flags, setFlags] = useState<PromptAuditFlag[]>([]);
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [reviewedFilter, setReviewedFilter] = useState<string>("all");
  const [expandedFlag, setExpandedFlag] = useState<number | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [collectMsg, setCollectMsg] = useState("");

  const fetchSummary = useCallback(async () => {
    try {
      const res = await getPromptAuditSummary(dateFrom, dateTo);
      setSummary(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터를 불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo]);

  const fetchFlags = useCallback(async () => {
    try {
      const sev = severityFilter === "all" ? undefined : severityFilter;
      const rev =
        reviewedFilter === "all"
          ? undefined
          : reviewedFilter === "unreviewed"
            ? false
            : true;
      const res = await getPromptAuditFlags(sev, rev, 100);
      setFlags(res.flags);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터를 불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, [severityFilter, reviewedFilter]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    setLoading(true);
    if (tab === "usage") fetchSummary();
    else fetchFlags();
  }, [tab, router, fetchSummary, fetchFlags]);

  // Auto-refresh
  useEffect(() => {
    const fn = tab === "usage" ? fetchSummary : fetchFlags;
    const timer = setInterval(fn, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [tab, fetchSummary, fetchFlags]);

  const handleCollect = async () => {
    setCollectMsg("");
    try {
      const res = await triggerPromptAudit();
      setCollectMsg(`${res.analyzed}건 분석 완료`);
      if (tab === "usage") fetchSummary();
      else fetchFlags();
    } catch (err) {
      setCollectMsg(err instanceof Error ? err.message : "수집 실패");
    }
  };

  const handleReview = async (flagId: number) => {
    try {
      await reviewPromptFlag(flagId);
      setFlags((prev) =>
        prev.map((f) =>
          f.id === flagId ? { ...f, reviewed: true, reviewed_at: new Date().toISOString() } : f
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "검토 처리 실패");
    }
  };

  const tabBtnClass = (t: Tab) =>
    `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      tab === t
        ? "bg-[var(--primary)] text-white"
        : "bg-[var(--surface)] text-[var(--text-secondary)] border border-[var(--border-strong)] hover:bg-[var(--bg)]"
    }`;

  // Security tab stats
  const totalFlags = flags.length;
  const unreviewed = flags.filter((f) => !f.reviewed).length;
  const criticalHigh = flags.filter(
    (f) => f.severity === "critical" || f.severity === "high"
  ).length;

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-[var(--error-light)] px-4 py-3 text-sm text-[var(--error)]">{error}</div>
        )}

        {/* Toolbar */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <div className="flex gap-2">
            <button className={tabBtnClass("usage")} onClick={() => setTab("usage")}>
              사용 분석
            </button>
            <button className={tabBtnClass("security")} onClick={() => setTab("security")}>
              보안 감사
            </button>
          </div>

          <div className="ml-auto flex items-center gap-3">
            {tab === "usage" && (
              <>
                <label className="text-xs text-[var(--text-muted)]">기간:</label>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                />
                <span className="text-[var(--text-muted)]">~</span>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                />
              </>
            )}
            <button
              onClick={handleCollect}
              className="rounded-md border border-[var(--primary)] bg-[var(--primary-light)] px-3 py-1.5 text-sm font-medium text-[var(--primary)] hover:bg-[var(--primary-light)] transition-colors"
            >
              수집 실행
            </button>
          </div>
        </div>

        {collectMsg && (
          <div className="mb-4 rounded-md bg-[var(--success-light)] px-4 py-2 text-sm text-[var(--success)]">
            {collectMsg}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
            데이터를 불러오는 중...
          </div>
        ) : tab === "usage" ? (
          /* ======================== TAB 1: Usage Analysis ======================== */
          summary ? (
            <>
              {/* Stats cards */}
              <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
                <StatsCard label="총 프롬프트" value={(summary.total_prompts ?? 0).toLocaleString()} />
                <StatsCard label="사용자 수" value={summary.users.length} />
                <StatsCard label="플래그 수" value={summary.total_flags} />
              </div>

              {/* Category bar chart */}
              <div className="mb-8 rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                <div className="border-b border-[var(--border)] px-4 py-3">
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">카테고리별 사용량</h2>
                  <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                    {summary.date_from} ~ {summary.date_to} / 60초 자동 갱신
                  </p>
                </div>
                <div className="px-4 py-4 overflow-x-auto">
                  <CategoryBarChart data={summary.category_totals} />
                </div>
              </div>

              {/* Per-user table */}
              <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                <div className="border-b border-[var(--border)] px-4 py-3">
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                    사용자별 프롬프트 분석 ({summary.users.length}명)
                  </h2>
                </div>
                {summary.users.length === 0 ? (
                  <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
                    감사 데이터가 없습니다
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-[var(--border)]">
                      <thead className="bg-[var(--bg)]">
                        <tr>
                          <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                            사용자
                          </th>
                          <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">
                            총 프롬프트
                          </th>
                          <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                            주요 카테고리
                          </th>
                          <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">
                            플래그 수
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border)]">
                        {summary.users.map((u) => (
                          <tr key={u.username} className="hover:bg-[var(--bg)]">
                            <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                              {u.user_name ?? u.username}
                              <span className="ml-1 text-xs text-[var(--text-muted)]">({u.username})</span>
                            </td>
                            <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                              {(u.total_prompts ?? 0).toLocaleString()}
                            </td>
                            <td className="px-4 py-3 text-sm">
                              <div className="flex gap-1.5">
                                {topCategories(u.category_counts).map(({ cat, count }) => (
                                  <span
                                    key={cat}
                                    className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium text-white"
                                    style={{
                                      backgroundColor: CATEGORY_COLORS[cat] || "#6b7280",
                                    }}
                                  >
                                    {CATEGORY_LABELS[cat] || cat} {count}
                                  </span>
                                ))}
                                {topCategories(u.category_counts).length === 0 && (
                                  <span className="text-xs text-[var(--text-muted)]">-</span>
                                )}
                              </div>
                            </td>
                            <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums">
                              {u.flagged_count > 0 ? (
                                <span className="font-medium text-[var(--danger)]">{u.flagged_count}</span>
                              ) : (
                                <span className="text-[var(--text-muted)]">0</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
              감사 데이터가 없습니다
            </div>
          )
        ) : (
          /* ======================== TAB 2: Security Audit ======================== */
          <>
            {/* Summary stats */}
            <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <StatsCard label="전체 플래그" value={totalFlags} />
              <StatsCard label="미검토" value={unreviewed} />
              <StatsCard label="심각 (Critical+High)" value={criticalHigh} />
            </div>

            {/* Filter bar */}
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <label className="text-xs font-medium text-[var(--text-muted)]">심각도:</label>
              <select
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
                className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
              >
                <option value="all">전체</option>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>

              <label className="ml-4 text-xs font-medium text-[var(--text-muted)]">검토 상태:</label>
              <select
                value={reviewedFilter}
                onChange={(e) => setReviewedFilter(e.target.value)}
                className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
              >
                <option value="all">전체</option>
                <option value="unreviewed">미검토</option>
                <option value="reviewed">검토완료</option>
              </select>
            </div>

            {/* Flags table */}
            <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
              <div className="border-b border-[var(--border)] px-4 py-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                  보안 플래그 ({flags.length}건)
                </h2>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">60초 자동 갱신</p>
              </div>
              {flags.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
                  감사 데이터가 없습니다
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-[var(--border)]">
                    <thead className="bg-[var(--bg)]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                          시간
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                          사용자
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                          심각도
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                          카테고리
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                          프롬프트 발췌
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">
                          사유
                        </th>
                        <th className="px-4 py-3 text-center text-xs font-medium uppercase text-[var(--text-muted)]">
                          상태
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border)]">
                      {flags.map((f) => (
                        <tr key={f.id} className="hover:bg-[var(--bg)]">
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                            {formatDateTime(f.flagged_at)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                            {f.username}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            {severityBadge(f.severity)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                            <span
                              className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium text-white"
                              style={{
                                backgroundColor: CATEGORY_COLORS[f.category] || "#6b7280",
                              }}
                            >
                              {CATEGORY_LABELS[f.category] || f.category}
                            </span>
                          </td>
                          <td className="max-w-xs px-4 py-3 text-sm">
                            <button
                              onClick={() =>
                                setExpandedFlag(expandedFlag === f.id ? null : f.id)
                              }
                              className="text-left"
                            >
                              <code className="block rounded bg-[var(--surface-hover)] px-2 py-1 font-mono text-xs text-[var(--text-secondary)]">
                                {expandedFlag === f.id
                                  ? f.prompt_excerpt
                                  : f.prompt_excerpt.length > 100
                                    ? f.prompt_excerpt.slice(0, 100) + "..."
                                    : f.prompt_excerpt}
                              </code>
                            </button>
                          </td>
                          <td className="max-w-[200px] px-4 py-3 text-sm text-[var(--text-secondary)]">
                            {f.reason}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-center text-sm">
                            {f.reviewed ? (
                              <span className="inline-flex items-center rounded-full bg-[var(--success-light)] px-2.5 py-0.5 text-xs font-medium text-[var(--success)]">
                                검토완료
                              </span>
                            ) : (
                              <button
                                onClick={() => handleReview(f.id)}
                                className="rounded-md border border-[var(--primary)] bg-[var(--primary-light)] px-2.5 py-1 text-xs font-medium text-[var(--primary)] hover:bg-[var(--primary-light)] transition-colors"
                              >
                                검토완료
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </main>
    </>
  );
}
