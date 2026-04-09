"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getActiveSessions,
  adminTerminateSession,
  getExtensionRequests,
  approveExtension,
  rejectExtension,
  type Session,
  type ExtensionRequest,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import StatsCard from "@/components/stats-card";
import SessionTable from "@/components/session-table";

const REFRESH_INTERVAL = 10_000;

export default function DashboardPage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [extensionRequests, setExtensionRequests] = useState<ExtensionRequest[]>([]);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await getActiveSessions();
      setSessions(data.sessions);
      setTotal(data.total);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 조회 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchExtensions = useCallback(async () => {
    try {
      const res = await getExtensionRequests();
      setExtensionRequests(res.requests);
    } catch {
      /* ignore — extensions are supplementary */
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    fetchSessions();
    fetchExtensions();
    const timer = setInterval(() => {
      fetchSessions();
      fetchExtensions();
    }, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchSessions, fetchExtensions]);

  // Auto-dismiss success message after 3 seconds
  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(""), 3000);
    return () => clearTimeout(t);
  }, [success]);

  const runningSessions = sessions.filter((s) => s.pod_status === "running").length;
  const uniqueUsers = new Set(sessions.map((s) => s.username)).size;

  const today = new Date().toISOString().slice(0, 10);
  const todaySessions = sessions.filter(
    (s) => s.started_at.slice(0, 10) === today
  ).length;

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">
            {error}
          </div>
        )}

        {success && (
          <div className="mb-6 rounded-md bg-[var(--success-light)] px-4 py-3 text-sm text-[var(--success)]">
            {success}
          </div>
        )}

        {/* Stats */}
        <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatsCard label="활성 세션" value={runningSessions} />
          <StatsCard label="접속 사용자" value={uniqueUsers} />
          <StatsCard label="오늘의 세션" value={todaySessions} />
        </div>

        {/* Sessions Table */}
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">
              활성 세션 ({total})
            </h2>
            <span className="text-xs text-[var(--text-muted)]">10초마다 자동 갱신</span>
          </div>
          <SessionTable sessions={sessions} loading={loading} onTerminate={async (sessionId) => {
            if (confirm('이 세션의 Pod을 종료하시겠습니까?\n대화 내용은 EFS에 백업되어 복원 가능합니다.')) {
              try {
                await adminTerminateSession(sessionId);
                fetchSessions();
              } catch (e: unknown) {
                console.error(e);
              }
            }
          }} />
        </div>

        {/* 스케줄링 + 연장 요청 관리 */}
        <div className="mt-8 rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">스케줄링 관리</h2>
            <span className="text-xs text-[var(--text-muted)]">24/7 운영 — Pod 수명은 사용자별 TTL로 관리</span>
          </div>

          {/* 연장 요청 목록 */}
          <div className="p-4">
            <h3 className="mb-2 text-xs font-semibold uppercase text-[var(--text-muted)]">
              연장 요청
            </h3>
            {extensionRequests.length === 0 ? (
              <p className="text-sm text-[var(--text-muted)]">연장 요청이 없습니다.</p>
            ) : (
              <div className="space-y-2">
                {extensionRequests.map((req) => (
                  <div
                    key={req.id}
                    className={`flex items-center justify-between rounded-lg border p-3 ${
                      req.status === "pending"
                        ? "border-[var(--warning-light)] bg-[var(--warning-light)]"
                        : req.status === "approved"
                          ? "border-[var(--success-light)] bg-[var(--success-light)]"
                          : "border-[var(--border)] bg-[var(--bg)]"
                    }`}
                  >
                    <div>
                      <span className="text-sm font-medium">
                        {req.user_name || req.username}
                      </span>
                      <span className="ml-1 text-xs text-[var(--text-muted)]">
                        ({req.username})
                      </span>
                      <span className="ml-2 text-xs text-[var(--text-muted)]">
                        {req.requested_hours}시간 연장
                      </span>
                      <span className="ml-2 text-xs text-[var(--text-muted)]">
                        {new Date(req.requested_at).toLocaleString("ko-KR")}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {req.status === "pending" ? (
                        <>
                          <button
                            onClick={async () => {
                              await approveExtension(req.id);
                              fetchExtensions();
                            }}
                            className="rounded bg-[var(--success)] px-3 py-1 text-xs font-medium text-white hover:bg-[var(--success)]"
                          >
                            승인
                          </button>
                          <button
                            onClick={async () => {
                              await rejectExtension(req.id);
                              fetchExtensions();
                            }}
                            className="rounded bg-[var(--danger-light)] px-2 py-1 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)]"
                          >
                            거절
                          </button>
                        </>
                      ) : (
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            req.status === "approved"
                              ? "bg-[var(--success-light)] text-[var(--success)]"
                              : "bg-[var(--surface-hover)] text-[var(--text-muted)]"
                          }`}
                        >
                          {req.status === "approved" ? "승인됨" : "거절됨"}
                          {req.resolved_by && ` (${req.resolved_by})`}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </main>
    </>
  );
}
