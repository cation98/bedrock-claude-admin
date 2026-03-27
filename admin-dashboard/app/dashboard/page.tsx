"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { getActiveSessions, adminTerminateSession, type Session } from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import StatsCard from "@/components/stats-card";
import SessionTable from "@/components/session-table";

const REFRESH_INTERVAL = 10_000;

export default function DashboardPage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    fetchSessions();
    const timer = setInterval(fetchSessions, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchSessions]);

  const user = getUser();

  const runningSessions = sessions.filter((s) => s.pod_status === "running").length;
  const uniqueUsers = new Set(sessions.map((s) => s.username)).size;

  const today = new Date().toISOString().slice(0, 10);
  const todaySessions = sessions.filter(
    (s) => s.started_at.slice(0, 10) === today
  ).length;

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
          <h1 className="text-lg font-bold text-gray-900">Claude Code Admin</h1>
          <div className="flex items-center gap-6">
            <nav className="flex gap-4 text-sm font-medium text-gray-600">
              <Link
                href="/dashboard"
                className="text-blue-600 border-b-2 border-blue-600 pb-0.5"
              >
                운용현황
              </Link>
              <Link
                href="/users"
                className="hover:text-gray-900 transition-colors"
              >
                사용자 관리
              </Link>
              <Link
                href="/usage"
                className="hover:text-gray-900 transition-colors"
              >
                토큰 사용량
              </Link>
              <Link
                href="/infra"
                className="hover:text-gray-900 transition-colors"
              >
                인프라
              </Link>
            </nav>
            <div className="flex items-center gap-3">
              {user && (
                <span className="text-sm text-gray-500">{user.name}</span>
              )}
              <button
                onClick={logout}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
              >
                로그아웃
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
            {error}
          </div>
        )}

        {/* Stats */}
        <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatsCard label="활성 세션" value={runningSessions} />
          <StatsCard label="접속 사용자" value={uniqueUsers} />
          <StatsCard label="오늘의 세션" value={todaySessions} />
        </div>

        {/* Sessions Table */}
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-900">
              활성 세션 ({total})
            </h2>
            <span className="text-xs text-gray-400">10초마다 자동 갱신</span>
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
      </main>
    </div>
  );
}
