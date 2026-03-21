"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  getActiveSessions,
  bulkCreateSessions,
  bulkDeleteSessions,
  type Session,
} from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import SessionTable from "@/components/session-table";

const REFRESH_INTERVAL = 10_000;

export default function WorkshopPage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Create form state
  const [usernames, setUsernames] = useState("");
  const [sessionType, setSessionType] = useState("workshop");
  const [creating, setCreating] = useState(false);

  // Terminate state
  const [terminating, setTerminating] = useState(false);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await getActiveSessions();
      setSessions(data.sessions);
      setTotal(data.total);
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

  function clearMessages() {
    setError("");
    setSuccess("");
  }

  async function handleCreate() {
    clearMessages();
    const names = usernames
      .split("\n")
      .map((n) => n.trim())
      .filter(Boolean);

    if (names.length === 0) {
      setError("사용자명을 입력해주세요.");
      return;
    }

    setCreating(true);
    try {
      const res = await bulkCreateSessions({
        usernames: names,
        session_type: sessionType,
      });
      setSuccess(`${res.total}개의 세션이 생성되었습니다.`);
      setUsernames("");
      fetchSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "세션 생성 실패");
    } finally {
      setCreating(false);
    }
  }

  async function handleTerminateAll() {
    clearMessages();
    const confirmed = window.confirm(
      "모든 활성 세션을 종료하시겠습니까?\n이 작업은 되돌릴 수 없습니다."
    );
    if (!confirmed) return;

    setTerminating(true);
    try {
      const res = await bulkDeleteSessions();
      setSuccess(`${res.terminated}개의 세션이 종료되었습니다.`);
      fetchSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "세션 종료 실패");
    } finally {
      setTerminating(false);
    }
  }

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
                className="hover:text-gray-900 transition-colors"
              >
                대시보드
              </Link>
              <Link
                href="/workshop"
                className="text-blue-600 border-b-2 border-blue-600 pb-0.5"
              >
                워크숍 관리
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
        {/* Messages */}
        {error && (
          <div className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
            {error}
          </div>
        )}
        {success && (
          <div className="mb-6 rounded-md bg-green-50 px-4 py-3 text-sm text-green-700">
            {success}
          </div>
        )}

        <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Start Workshop */}
          <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold text-gray-900">
              워크숍 시작
            </h2>

            <div className="space-y-4">
              <div>
                <label
                  htmlFor="usernames"
                  className="mb-1 block text-sm font-medium text-gray-700"
                >
                  사용자명 (한 줄에 하나씩)
                </label>
                <textarea
                  id="usernames"
                  rows={6}
                  value={usernames}
                  onChange={(e) => setUsernames(e.target.value)}
                  className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono"
                  placeholder={"user01\nuser02\nuser03"}
                />
              </div>

              <div>
                <label
                  htmlFor="sessionType"
                  className="mb-1 block text-sm font-medium text-gray-700"
                >
                  세션 유형
                </label>
                <select
                  id="sessionType"
                  value={sessionType}
                  onChange={(e) => setSessionType(e.target.value)}
                  className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                >
                  <option value="workshop">Workshop</option>
                  <option value="daily">Daily</option>
                </select>
              </div>

              <button
                onClick={handleCreate}
                disabled={creating}
                className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {creating ? "생성 중..." : "전체 세션 생성"}
              </button>
            </div>
          </div>

          {/* End Workshop */}
          <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold text-gray-900">
              워크숍 종료
            </h2>
            <p className="mb-4 text-sm text-gray-500">
              모든 활성 세션을 일괄 종료합니다. 실행 중인 모든 Pod가 삭제됩니다.
            </p>
            <div className="rounded-md bg-red-50 p-4">
              <p className="mb-3 text-sm font-medium text-red-800">
                현재 활성 세션: {total}개
              </p>
              <button
                onClick={handleTerminateAll}
                disabled={terminating || total === 0}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {terminating ? "종료 중..." : "전체 세션 종료"}
              </button>
            </div>
          </div>
        </div>

        {/* Active Sessions */}
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-900">
              워크숍 세션 ({total})
            </h2>
            <span className="text-xs text-gray-400">10초마다 자동 갱신</span>
          </div>
          <SessionTable sessions={sessions} loading={loading} />
        </div>
      </main>
    </div>
  );
}
