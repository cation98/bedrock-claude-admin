"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { getGalleryApps, getAppAnalytics, DeployedApp, AppAnalytics } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

const REFRESH_INTERVAL = 30_000;

function statusBadge(status: string) {
  const base = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (status) {
    case "running":  return <span className={`${base} bg-[var(--success-light)] text-[var(--success)]`}>실행중</span>;
    case "stopped":  return <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>중지</span>;
    case "creating": return <span className={`${base} bg-[var(--warning-light)] text-[var(--warning)]`}>생성중</span>;
    default:         return <span className={`${base} bg-[var(--error-light)] text-[var(--error)]`}>{status}</span>;
  }
}

function visibilityBadge(v: string) {
  const base = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  return v === "company"
    ? <span className={`${base} bg-[var(--primary-light)] text-[var(--primary)]`}>사내공개</span>
    : <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>비공개</span>;
}

function formatDate(iso: string | null) {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

/* ── Analytics Panel (expand on row click) ───────────────────── */
function AnalyticsPanel({ appName }: { appName: string }) {
  const [data, setData] = useState<AppAnalytics | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    getAppAnalytics(appName, 30)
      .then(setData)
      .catch((e) => setErr(e.message));
  }, [appName]);

  if (err) return <div className="px-6 py-3 text-xs text-[var(--danger)]">{err}</div>;
  if (!data) return <div className="px-6 py-3 text-xs text-[var(--text-muted)]">분석 데이터 로딩 중...</div>;

  const max = Math.max(...data.daily_trend.map((d) => d.views), 1);

  return (
    <div className="border-t border-[var(--border)] bg-[var(--bg)] px-6 py-4">
      {/* KPI 행 */}
      <div className="mb-4 grid grid-cols-5 gap-3">
        {[
          { label: "DAU", value: data.dau, sub: "오늘 활성 사용자" },
          { label: "WAU", value: data.wau, sub: "7일 활성 사용자" },
          { label: "MAU", value: data.mau, sub: "30일 활성 사용자" },
          { label: "총 조회수", value: data.total_views.toLocaleString(), sub: "누적" },
          { label: "순 방문자", value: data.total_unique_viewers.toLocaleString(), sub: "누적 고유" },
        ].map(({ label, value, sub }) => (
          <div key={label} className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-center">
            <div className="text-xs text-[var(--text-muted)]">{label}</div>
            <div className="mt-0.5 text-xl font-bold text-[var(--text-primary)]">{value}</div>
            <div className="text-[10px] text-[var(--text-muted)]">{sub}</div>
          </div>
        ))}
      </div>

      {/* 30일 일별 조회수 바 차트 */}
      <div>
        <div className="mb-1 text-xs font-medium text-[var(--text-secondary)]">최근 30일 일별 조회수</div>
        {data.daily_trend.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)]">조회 기록이 없습니다</div>
        ) : (
          <div className="flex h-16 items-end gap-0.5">
            {data.daily_trend.map((d) => {
              const h = Math.max(2, Math.round((d.views / max) * 56));
              return (
                <div
                  key={d.date}
                  title={`${d.date}: ${d.views}회 (${d.unique_users}명)`}
                  style={{ height: h }}
                  className="flex-1 min-w-[3px] max-w-[10px] rounded-t bg-[var(--primary)] opacity-70 hover:opacity-100 cursor-default"
                />
              );
            })}
          </div>
        )}
        <div className="mt-1 flex justify-between text-[9px] text-[var(--text-muted)]">
          {data.daily_trend.length > 0 && (
            <>
              <span>{data.daily_trend[0].date}</span>
              <span>{data.daily_trend[data.daily_trend.length - 1].date}</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AppsPage() {
  const router = useRouter();
  const [apps, setApps] = useState<DeployedApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sort, setSort] = useState("hot");
  const [expandedApp, setExpandedApp] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await getGalleryApps();
      setApps(res.apps);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load apps");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) { router.replace("/"); return; }
    fetchData();
    const timer = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  const runningCount = apps.filter((a) => a.status === "running").length;
  const totalDAU = apps.reduce((s, a) => s + (a.dau ?? 0), 0);
  const totalMAU = apps.reduce((s, a) => s + (a.mau ?? 0), 0);
  const totalViews = apps.reduce((s, a) => s + (a.view_count ?? 0), 0);

  const sorted = [...apps].sort((a, b) => {
    if (sort === "hot") return ((b.like_count ?? 0) * 3 + (b.dau ?? 0) * 2 + (b.unique_viewers ?? 0)) - ((a.like_count ?? 0) * 3 + (a.dau ?? 0) * 2 + (a.unique_viewers ?? 0));
    if (sort === "latest") return (b.created_at ?? "") > (a.created_at ?? "") ? 1 : -1;
    if (sort === "most_viewed") return (b.view_count ?? 0) - (a.view_count ?? 0);
    if (sort === "mau") return (b.mau ?? 0) - (a.mau ?? 0);
    return 0;
  });

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && <div className="mb-6 rounded-md bg-[var(--error-light)] px-4 py-3 text-sm text-[var(--error)]">{error}</div>}

        {/* Stats */}
        <div className="mb-6 grid grid-cols-5 gap-4">
          {[
            { label: "전체 앱", value: apps.length, color: "" },
            { label: "실행 중", value: runningCount, color: "text-[var(--success)]" },
            { label: "오늘 DAU (합계)", value: totalDAU, color: "text-[var(--primary)]" },
            { label: "MAU (합계)", value: totalMAU, color: "text-[var(--info)]" },
            { label: "총 조회수", value: totalViews.toLocaleString(), color: "text-[var(--info)]" },
          ].map(({ label, value, color }) => (
            <div key={label} className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
              <div className="text-sm text-[var(--text-muted)]">{label}</div>
              <div className={`mt-1 text-2xl font-bold ${color || "text-[var(--text-primary)]"}`}>{value}</div>
            </div>
          ))}
        </div>

        {/* Sort */}
        <div className="mb-4 flex items-center gap-2">
          <span className="text-xs text-[var(--text-muted)]">정렬:</span>
          {[
            { key: "hot",         label: "인기순" },
            { key: "mau",         label: "MAU순" },
            { key: "most_viewed", label: "조회순" },
            { key: "latest",      label: "최신순" },
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setSort(key)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${sort === key ? "bg-[var(--primary)] text-white" : "bg-[var(--surface)] border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"}`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Apps Table */}
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-base font-semibold text-[var(--text-primary)]">배포된 웹앱</h2>
            <p className="text-xs text-[var(--text-muted)]">앱 행을 클릭하면 30일 분석 데이터가 펼쳐집니다</p>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">데이터를 불러오는 중...</div>
          ) : sorted.length === 0 ? (
            <div className="py-12 text-center text-sm text-[var(--text-muted)]">배포된 앱이 없습니다.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-[var(--border)]">
                <thead className="bg-[var(--bg)]">
                  <tr>
                    {["앱 이름", "배포자", "상태", "공개", "DAU", "WAU", "MAU", "총조회", "배포일", ""].map((h) => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                  {sorted.map((a) => {
                    const isExpanded = expandedApp === a.app_name;
                    return (
                      <>
                        <tr
                          key={a.id}
                          className="cursor-pointer hover:bg-[var(--bg)]"
                          onClick={() => setExpandedApp(isExpanded ? null : a.app_name)}
                        >
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <a
                              href={a.app_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="font-medium text-[var(--primary)] hover:underline"
                            >
                              {a.app_name}
                            </a>
                            <div className="text-[10px] text-[var(--text-muted)]">v{a.version}</div>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-primary)]">
                            {a.author_name ?? a.owner_username}
                            <span className="ml-1 text-xs text-[var(--text-muted)]">({a.owner_username})</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">{statusBadge(a.status)}</td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">{visibilityBadge(a.visibility ?? "private")}</td>
                          <td className="whitespace-nowrap px-4 py-3 text-center">
                            <span className="text-sm font-bold text-[var(--primary)]">{a.dau ?? 0}</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-center text-sm text-[var(--text-secondary)]">{a.wau ?? 0}</td>
                          <td className="whitespace-nowrap px-4 py-3 text-center">
                            <span className="text-sm font-bold text-[var(--info)]">{a.mau ?? 0}</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-center text-sm text-[var(--text-secondary)]">
                            {(a.view_count ?? 0).toLocaleString()}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">{formatDate(a.created_at)}</td>
                          <td className="whitespace-nowrap px-4 py-3 text-xs text-[var(--text-muted)]">
                            {isExpanded ? "▲" : "▼"}
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr key={`${a.app_name}-analytics`}>
                            <td colSpan={10} className="p-0">
                              <AnalyticsPanel appName={a.app_name} />
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </>
  );
}
