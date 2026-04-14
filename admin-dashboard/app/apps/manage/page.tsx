"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getAdminAppList,
  adminStopApp,
  adminStartApp,
  adminRecallApp,
  adminReapproveApp,
  type AdminAppInfo,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

const REFRESH_INTERVAL = 15_000;

function shortNode(n: string | null) {
  if (!n) return "-";
  return n.replace(".ap-northeast-2.compute.internal", "");
}

function formatDate(iso: string | null) {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    running:   "bg-[var(--success-light)] text-[var(--success)]",
    stopped:   "bg-[var(--surface-hover)] text-[var(--text-muted)]",
    suspended: "bg-[var(--warning-light)] text-[var(--warning)]",
    creating:  "bg-[var(--info-light)] text-[var(--info)]",
    error:     "bg-[var(--danger-light)] text-[var(--danger)]",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${map[status] ?? "bg-[var(--surface-hover)] text-[var(--text-muted)]"}`}>
      {status}
    </span>
  );
}

function PodStatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-xs text-[var(--text-muted)]">-</span>;
  const isBad = ["CrashLoopBackOff", "ImagePullBackOff", "Error", "Failed", "Unknown"].includes(status);
  const cls = isBad
    ? "bg-[var(--danger-light)] text-[var(--danger)]"
    : status === "Running"
    ? "bg-[var(--success-light)] text-[var(--success)]"
    : "bg-[var(--warning-light)] text-[var(--warning)]";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
}

type FilterStatus = "all" | "running" | "stopped" | "suspended" | "error";

export default function AppsManagePage() {
  const router = useRouter();
  const [apps, setApps] = useState<AdminAppInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [filter, setFilter] = useState<FilterStatus>("all");
  const [search, setSearch] = useState("");
  const [actioning, setActioning] = useState<string>("");
  const [collectedAt, setCollectedAt] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const res = await getAdminAppList(filter === "all" ? undefined : filter);
      setApps(res.apps);
      setCollectedAt(res.collected_at);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 로드 실패");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    if (!isAuthenticated()) { router.replace("/"); return; }
    fetchData();
    const t = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(t);
  }, [router, fetchData]);

  const handleStop = async (app: AdminAppInfo) => {
    if (!confirm(`[${app.owner_name ?? app.owner_username}] ${app.app_name} 앱을 중지합니까?\nPod이 종료되고 외부 접근이 차단됩니다.`)) return;
    const key = `${app.owner_username}/${app.app_name}`;
    setActioning(key);
    try {
      await adminStopApp(app.owner_username, app.app_name);
      setSuccess(`${app.app_name} 중지됨`);
      fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "중지 실패");
    } finally {
      setActioning("");
    }
  };

  // stopped 앱만 재시작 (승인된 앱, Pod만 올림)
  const handleRestart = async (app: AdminAppInfo) => {
    if (!confirm(
      `[재시작] ${app.owner_name ?? app.owner_username} / ${app.app_name}\n\n` +
      `이미 승인된 앱입니다. Pod를 다시 시작합니다.`
    )) return;
    const key = `${app.owner_username}/${app.app_name}`;
    setActioning(key);
    try {
      await adminStartApp(app.owner_username, app.app_name);
      setSuccess(`${app.app_name} 재시작됨`);
      fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "재시작 실패");
    } finally {
      setActioning("");
    }
  };

  // suspended 앱 → pending_approval로 전환 (검토 페이지에서 승인해야 서비스 재개)
  const handleReapprove = async (app: AdminAppInfo) => {
    if (!confirm(
      `[재승인 요청] ${app.owner_name ?? app.owner_username} / ${app.app_name}\n\n` +
      `배포 승인 대기 상태로 전환합니다.\n` +
      `'배포 승인' 페이지에서 앱 내용을 검토한 뒤 명시적으로 승인해야 서비스가 재개됩니다.`
    )) return;
    const key = `${app.owner_username}/${app.app_name}`;
    setActioning(key);
    try {
      await adminReapproveApp(app.owner_username, app.app_name);
      setSuccess(`${app.app_name} → 승인 대기 전환 완료. 배포 승인 페이지에서 검토하세요.`);
      fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "재승인 요청 실패");
    } finally {
      setActioning("");
    }
  };

  const handleRecall = async (app: AdminAppInfo) => {
    if (!confirm(
      `[배포 회수] ${app.owner_name ?? app.owner_username} / ${app.app_name}\n\n` +
      `갤러리에서 제거되고 외부 접근이 차단됩니다.\n` +
      `사용자의 코드와 데이터는 보존됩니다.\n` +
      `사용자는 본인 터미널에서 재배포할 수 있습니다.`
    )) return;
    const key = `${app.owner_username}/${app.app_name}`;
    setActioning(key);
    try {
      await adminRecallApp(app.owner_username, app.app_name);
      setSuccess(`${app.app_name} 배포 회수 완료`);
      fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "배포 회수 실패");
    } finally {
      setActioning("");
    }
  };

  const filtered = apps.filter((a) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      a.app_name.toLowerCase().includes(q) ||
      a.owner_username.toLowerCase().includes(q) ||
      (a.owner_name ?? "").toLowerCase().includes(q)
    );
  });

  const counts = {
    all: apps.length,
    running: apps.filter((a) => a.status === "running").length,
    stopped: apps.filter((a) => a.status === "stopped").length,
    suspended: apps.filter((a) => a.status === "suspended").length,
    error: apps.filter((a) => a.pod_status && ["CrashLoopBackOff", "ImagePullBackOff", "Error"].includes(a.pod_status)).length,
  };

  const FILTERS: { key: FilterStatus; label: string }[] = [
    { key: "all",       label: "전체" },
    { key: "running",   label: "실행 중" },
    { key: "stopped",   label: "중지" },
    { key: "suspended", label: "회수" },
    { key: "error",     label: "오류" },
  ];

  return (
    <>
      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-4 rounded-md bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">{error}</div>
        )}
        {success && (
          <div className="mb-4 rounded-md bg-[var(--success-light)] px-4 py-3 text-sm text-[var(--success)]">{success}</div>
        )}

        {/* Header */}
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-[var(--text-primary)]">사용자 배포 앱 관리</h1>
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              user-apps-workers 노드그룹 · {collectedAt && new Date(collectedAt).toLocaleTimeString("ko-KR")} 기준 · 15초 갱신
            </p>
          </div>
          <button
            onClick={fetchData}
            className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
          >
            새로고침
          </button>
        </div>

        {/* Stats */}
        <div className="mb-6 grid grid-cols-5 gap-3">
          {FILTERS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`rounded-lg border p-3 text-left transition-colors ${filter === key ? "border-[var(--primary)] bg-[var(--primary-light)]" : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-hover)]"}`}
            >
              <div className="text-xs text-[var(--text-muted)]">{label}</div>
              <div className={`mt-1 text-2xl font-bold ${key === "error" && counts[key] > 0 ? "text-[var(--danger)]" : "text-[var(--text-primary)]"}`}>
                {counts[key]}
              </div>
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="mb-4">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="앱 이름, 배포자 검색..."
            className="w-full max-w-xs rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
          />
        </div>

        {/* Table */}
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
          <div className="border-b border-[var(--border)] px-4 py-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">배포 앱 목록 ({filtered.length}개)</h2>
            <span className="text-xs text-[var(--text-muted)]">노드: user-apps-workers (t3.medium, bin-packing)</span>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">불러오는 중...</div>
          ) : filtered.length === 0 ? (
            <div className="py-12 text-center text-sm text-[var(--text-muted)]">앱이 없습니다.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-[var(--bg)] text-xs text-[var(--text-muted)]">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium">앱</th>
                    <th className="px-4 py-3 text-left font-medium">배포자</th>
                    <th className="px-4 py-3 text-left font-medium">DB 상태</th>
                    <th className="px-4 py-3 text-left font-medium">Pod 상태</th>
                    <th className="px-4 py-3 text-left font-medium">노드 / IP</th>
                    <th className="px-4 py-3 text-center font-medium">Restarts</th>
                    <th className="px-4 py-3 text-left font-medium">공개</th>
                    <th className="px-4 py-3 text-center font-medium">조회</th>
                    <th className="px-4 py-3 text-left font-medium">배포일</th>
                    <th className="px-4 py-3 text-right font-medium">관리</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {filtered.map((app) => {
                    const key = `${app.owner_username}/${app.app_name}`;
                    const busy = actioning === key;
                    const isRunning = app.status === "running";
                    const isStopped = app.status === "stopped";
                    const hasError = app.pod_status && ["CrashLoopBackOff", "ImagePullBackOff", "Error"].includes(app.pod_status);
                    return (
                      <tr key={app.id} className={`hover:bg-[var(--surface-hover)] ${hasError ? "bg-[var(--danger-light)]/10" : ""}`}>
                        <td className="px-4 py-3">
                          <a
                            href={app.app_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-medium text-[var(--primary)] hover:underline"
                          >
                            {app.app_name}
                          </a>
                          <div className="text-[10px] font-mono text-[var(--text-muted)]">v{app.version} · :{app.app_port}</div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="font-medium text-[var(--text-primary)]">{app.owner_name ?? app.owner_username}</div>
                          <div className="text-[10px] text-[var(--text-muted)]">{app.owner_username}</div>
                        </td>
                        <td className="px-4 py-3"><StatusBadge status={app.status} /></td>
                        <td className="px-4 py-3"><PodStatusBadge status={app.pod_status} /></td>
                        <td className="px-4 py-3 font-mono text-xs text-[var(--text-secondary)]">
                          <div>{shortNode(app.node_name)}</div>
                          <div className="text-[var(--text-muted)]">{app.pod_ip ?? "-"}</div>
                        </td>
                        <td className="px-4 py-3 text-center font-mono">
                          <span className={app.restarts > 5 ? "text-[var(--danger)]" : "text-[var(--text-muted)]"}>
                            {app.restarts}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${app.visibility === "company" ? "bg-[var(--primary-light)] text-[var(--primary)]" : "bg-[var(--surface-hover)] text-[var(--text-muted)]"}`}>
                            {app.visibility}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-center text-[var(--text-secondary)]">
                          {(app.view_count ?? 0).toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-[var(--text-muted)]">{formatDate(app.created_at)}</td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex justify-end gap-1.5">
                            {/* 실행 중: 앱 보기 + 중지 + 배포 회수 */}
                            {isRunning && (
                              <>
                                <a
                                  href={app.app_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="rounded border border-[var(--primary)] px-2 py-1 text-xs font-medium text-[var(--primary)] hover:bg-[var(--primary-light)]"
                                >
                                  앱 보기 ↗
                                </a>
                                <button
                                  disabled={busy}
                                  onClick={() => handleStop(app)}
                                  className="rounded border border-[var(--border)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] disabled:opacity-50"
                                >
                                  {busy ? "..." : "중지"}
                                </button>
                                <button
                                  disabled={busy}
                                  onClick={() => handleRecall(app)}
                                  className="rounded border border-[var(--warning)] px-2 py-1 text-xs font-medium text-[var(--warning)] hover:bg-[var(--warning-light)] disabled:opacity-50"
                                >
                                  {busy ? "..." : "배포 회수"}
                                </button>
                              </>
                            )}
                            {/* 중지됨(사용자/관리자 중지): 재시작 가능 */}
                            {isStopped && (
                              <>
                                <button
                                  disabled={busy}
                                  onClick={() => handleRestart(app)}
                                  className="rounded border border-[var(--success)] px-2 py-1 text-xs font-medium text-[var(--success)] hover:bg-[var(--success-light)] disabled:opacity-50"
                                >
                                  {busy ? "..." : "재시작"}
                                </button>
                                <button
                                  disabled={busy}
                                  onClick={() => handleRecall(app)}
                                  className="rounded border border-[var(--warning)] px-2 py-1 text-xs font-medium text-[var(--warning)] hover:bg-[var(--warning-light)] disabled:opacity-50"
                                >
                                  {busy ? "..." : "배포 회수"}
                                </button>
                              </>
                            )}
                            {/* 회수됨(suspended): 검토 후 재승인 필요 */}
                            {app.status === "suspended" && (
                              <div className="flex flex-col items-end gap-1">
                                <button
                                  disabled={busy}
                                  onClick={() => handleReapprove(app)}
                                  className="rounded border border-[var(--info)] px-2 py-1 text-xs font-medium text-[var(--info)] hover:bg-[var(--info-light)] disabled:opacity-50"
                                >
                                  {busy ? "..." : "재승인 요청"}
                                </button>
                                <span className="text-[9px] text-[var(--text-muted)]">승인 페이지에서 검토 후 활성화</span>
                              </div>
                            )}
                            {/* 대기 중: 승인 페이지로 이동 */}
                            {app.status === "pending_approval" && (
                              <a
                                href="/apps/pending"
                                className="rounded border border-[var(--primary)] px-2 py-1 text-xs font-medium text-[var(--primary)] hover:bg-[var(--primary-light)]"
                              >
                                검토하기 →
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Policy Info */}
        <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 py-3 text-xs text-[var(--text-muted)]">
          <span className="font-medium text-[var(--text-secondary)]">노드 정책</span>
          {" · "}
          nodegroup: <span className="font-mono">user-apps-workers</span> (t3.medium)
          {" · "}
          taint: <span className="font-mono">dedicated=user-apps:NoSchedule</span>
          {" · "}
          스케줄링: <span className="font-mono">bin-packing (least-waste)</span>
          {" · "}
          앱당 리소스: <span className="font-mono">CPU 250m / Mem 512Mi → 노드당 약 6~7개</span>
          {" · "}
          Cluster Autoscaler: min=0 / max=5
        </div>
      </main>
    </>
  );
}
