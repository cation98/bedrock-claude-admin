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
      return <span className={`${base} bg-green-100 text-green-700`}>{status}</span>;
    case "stopped":
      return <span className={`${base} bg-gray-100 text-gray-500`}>{status}</span>;
    case "creating":
      return <span className={`${base} bg-yellow-100 text-yellow-700`}>{status}</span>;
    default:
      return <span className={`${base} bg-red-100 text-red-500`}>{status}</span>;
  }
}

function visibilityBadge(visibility: string) {
  const base = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (visibility) {
    case "company":
      return <span className={`${base} bg-blue-100 text-blue-700`}>{visibility}</span>;
    case "private":
    default:
      return <span className={`${base} bg-gray-100 text-gray-500`}>{visibility}</span>;
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
          <div className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
            {error}
          </div>
        )}

        {/* Stats */}
        <div className="mb-6 grid grid-cols-5 gap-4">
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="text-sm text-gray-500">전체 앱</div>
            <div className="mt-1 text-2xl font-bold text-gray-900">{apps.length}</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="text-sm text-gray-500">실행 중</div>
            <div className="mt-1 text-2xl font-bold text-green-600">{runningCount}</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="text-sm text-gray-500">중지됨</div>
            <div className="mt-1 text-2xl font-bold text-gray-400">{apps.length - runningCount}</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="text-sm text-gray-500">총 조회수</div>
            <div className="mt-1 text-2xl font-bold text-purple-600">{totalViews.toLocaleString()}</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="text-sm text-gray-500">활성 접속자</div>
            <div className="mt-1 text-2xl font-bold text-indigo-600">{totalViewers.toLocaleString()}</div>
          </div>
        </div>

        {/* Apps Table */}
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-4 py-3">
            <h2 className="text-base font-semibold text-gray-900">배포된 웹앱</h2>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-gray-400">
              데이터를 불러오는 중...
            </div>
          ) : apps.length === 0 ? (
            <div className="py-12 text-center text-sm text-gray-400">
              배포된 앱이 없습니다.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      앱 이름
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      배포자
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      상태
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      공개
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      버전
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-gray-500">
                      포트
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-gray-500">
                      조회수
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-gray-500">
                      접속자
                    </th>
                    <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-gray-500">
                      접근자
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Pod
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      배포일
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 bg-white">
                  {apps.map((a) => (
                    <tr key={a.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <a
                          href={a.app_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-blue-600 hover:underline"
                        >
                          {a.app_name}
                        </a>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        {a.owner_name || a.owner_username}
                        <span className="ml-1 text-xs text-gray-400">({a.owner_username})</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        {statusBadge(a.status)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        {visibilityBadge(a.visibility ?? "private")}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                        {a.version}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-gray-500 font-mono">
                        {a.app_port ?? "-"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-gray-600">
                        {(a.view_count ?? 0).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-gray-600">
                        {(a.unique_viewers ?? 0).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-gray-600">
                        {a.acl_count ?? 0}명
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs font-mono text-gray-400">
                        {a.pod_name}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
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
