"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { getAdminApps, getGalleryApps, DeployedApp } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

const REFRESH_INTERVAL = 30_000;

function statusBadge(status: string) {
  const base = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (status) {
    case "running":
      return <span className={`${base} bg-[var(--success-light)] text-[var(--success)]`}>{status}</span>;
    case "stopped":
      return <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>{status}</span>;
    case "creating":
      return <span className={`${base} bg-[var(--warning-light)] text-[var(--warning)]`}>{status}</span>;
    default:
      return <span className={`${base} bg-[var(--error-light)] text-[var(--error)]`}>{status}</span>;
  }
}

function visibilityBadge(visibility: string) {
  const base = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (visibility) {
    case "company":
      return <span className={`${base} bg-[var(--primary-light)] text-[var(--primary)]`}>{visibility}</span>;
    case "private":
    default:
      return <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>{visibility}</span>;
  }
}

function formatDate(iso: string | null) {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

export default function AppsPage() {
  const router = useRouter();
  const [apps, setApps] = useState<DeployedApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    fetchData();
    const timer = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  const runningCount = apps.filter((a) => a.status === "running").length;
  const totalViews = apps.reduce((sum, a) => sum + (a.view_count ?? 0), 0);
  const totalViewers = apps.reduce((sum, a) => sum + (a.unique_viewers ?? 0), 0);

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-[var(--error-light)] px-4 py-3 text-sm text-[var(--error)]">
            {error}
          </div>
        )}

        {/* Stats */}
        <div className="mb-6 grid grid-cols-5 gap-4">
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">전체 앱</div>
            <div className="mt-1 text-2xl font-bold text-[var(--text-primary)]">{apps.length}</div>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">실행 중</div>
            <div className="mt-1 text-2xl font-bold text-[var(--success)]">{runningCount}</div>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">중지됨</div>
            <div className="mt-1 text-2xl font-bold text-[var(--text-muted)]">{apps.length - runningCount}</div>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">총 조회수</div>
            <div className="mt-1 text-2xl font-bold text-[var(--info)]">{totalViews.toLocaleString()}</div>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">활성 접속자</div>
            <div className="mt-1 text-2xl font-bold text-[var(--info)]">{totalViewers.toLocaleString()}</div>
          </div>
        </div>

        {/* Apps Table */}
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-base font-semibold text-[var(--text-primary)]">배포된 웹앱</h2>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
              데이터를 불러오는 중...
            </div>
          ) : apps.length === 0 ? (
            <div className="py-12 text-center text-sm text-[var(--text-muted)]">
              배포된 앱이 없습니다.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-[var(--border)]">
                <thead className="bg-[var(--bg)]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      앱 이름
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      배포자
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      상태
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      공개
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      버전
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      포트
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      조회수
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      접속자
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      접근자
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      Pod
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                      배포일
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                  {apps.map((a) => (
                    <tr key={a.id} className="hover:bg-[var(--bg)]">
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <a
                          href={a.app_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-[var(--primary)] hover:underline"
                        >
                          {a.app_name}
                        </a>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-primary)]">
                        {a.owner_name || a.owner_username}
                        <span className="ml-1 text-xs text-[var(--text-muted)]">({a.owner_username})</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        {statusBadge(a.status)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        {visibilityBadge(a.visibility ?? "private")}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                        {a.version}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-[var(--text-muted)] font-mono">
                        {a.app_port ?? "-"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-[var(--text-secondary)]">
                        {(a.view_count ?? 0).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-[var(--text-secondary)]">
                        {(a.unique_viewers ?? 0).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-[var(--text-secondary)]">
                        {a.acl_count ?? 0}명
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs font-mono text-[var(--text-muted)]">
                        {a.pod_name}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                        {formatDate(a.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </>
  );
}
