"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  getUsers,
  getPendingUsers,
  approveUser,
  updateUserTtl,
  revokeUser,
  type User,
} from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";

const REFRESH_INTERVAL = 10_000;

const TTL_OPTIONS: { value: string; label: string }[] = [
  { value: "unlimited", label: "만료없음" },
  { value: "30d", label: "30일" },
  { value: "7d", label: "7일" },
  { value: "1d", label: "1일" },
  { value: "8h", label: "8시간" },
  { value: "4h", label: "4시간" },
];

const TTL_LABEL: Record<string, string> = {
  unlimited: "만료없음",
  "30d": "30일",
  "7d": "7일",
  "1d": "1일",
  "8h": "8시간",
  "4h": "4시간",
};

function roleBadge(role: string) {
  const base =
    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (role) {
    case "admin":
      return (
        <span className={`${base} bg-purple-100 text-purple-700`}>{role}</span>
      );
    case "user":
      return (
        <span className={`${base} bg-blue-100 text-blue-700`}>{role}</span>
      );
    default:
      return (
        <span className={`${base} bg-gray-100 text-gray-500`}>{role}</span>
      );
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function UsersPage() {
  const router = useRouter();
  const [pendingUsers, setPendingUsers] = useState<User[]>([]);
  const [approvedUsers, setApprovedUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // TTL for the approval dropdown per pending user (default: "4h")
  const [pendingTtl, setPendingTtl] = useState<Record<number, string>>({});

  const fetchData = useCallback(async () => {
    try {
      const [pendingRes, allRes] = await Promise.all([
        getPendingUsers(),
        getUsers(),
      ]);
      setPendingUsers(pendingRes.users);
      setApprovedUsers(allRes.users.filter((u) => u.is_approved));
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
    fetchData();
    const timer = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  const user = getUser();

  function clearMessages() {
    setError("");
    setSuccess("");
  }

  async function handleApprove(userId: number) {
    clearMessages();
    const ttl = pendingTtl[userId] ?? "4h";
    try {
      await approveUser(userId, ttl);
      setSuccess("사용자가 승인되었습니다.");
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "승인 실패");
    }
  }

  async function handleTtlChange(userId: number, newTtl: string) {
    clearMessages();
    try {
      await updateUserTtl(userId, newTtl);
      setSuccess("TTL이 변경되었습니다.");
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "TTL 변경 실패");
    }
  }

  async function handleRevoke(userId: number, username: string) {
    clearMessages();
    const confirmed = window.confirm(
      `${username} 사용자의 승인을 취소하시겠습니까?`
    );
    if (!confirmed) return;

    try {
      await revokeUser(userId);
      setSuccess("승인이 취소되었습니다.");
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "승인 취소 실패");
    }
  }

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
          <h1 className="text-lg font-bold text-gray-900">
            Claude Code Admin
          </h1>
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
                className="hover:text-gray-900 transition-colors"
              >
                워크숍 관리
              </Link>
              <Link
                href="/users"
                className="text-blue-600 border-b-2 border-blue-600 pb-0.5"
              >
                사용자 관리
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

        {loading ? (
          <div className="flex items-center justify-center py-12 text-gray-400">
            데이터를 불러오는 중...
          </div>
        ) : (
          <div className="space-y-8">
            {/* Pending Users */}
            <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
              <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                <h2 className="text-sm font-semibold text-gray-900">
                  승인 대기 ({pendingUsers.length})
                </h2>
                <span className="text-xs text-gray-400">
                  10초마다 자동 갱신
                </span>
              </div>

              {pendingUsers.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-gray-400">
                  승인 대기 중인 사용자가 없습니다.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          이름 (사번)
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          소속
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          역할
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          Pod TTL
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          최근 로그인
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          작업
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200 bg-white">
                      {pendingUsers.map((u) => (
                        <tr key={u.id} className="hover:bg-gray-50">
                          <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                            {u.name ?? u.username}
                            <span className="ml-1 text-xs text-gray-400">({u.username})</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                            {[u.region_name, u.team_name, u.job_name].filter(Boolean).join(" / ") || "-"}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            {roleBadge(u.role)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <select
                              value={pendingTtl[u.id] ?? "4h"}
                              onChange={(e) =>
                                setPendingTtl((prev) => ({
                                  ...prev,
                                  [u.id]: e.target.value,
                                }))
                              }
                              className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              {TTL_OPTIONS.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                            {formatDate(u.last_login_at)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <button
                              onClick={() => handleApprove(u.id)}
                              className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-blue-700 transition-colors"
                            >
                              승인
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Approved Users */}
            <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
              <div className="border-b border-gray-200 px-4 py-3">
                <h2 className="text-sm font-semibold text-gray-900">
                  허용 목록 ({approvedUsers.length})
                </h2>
              </div>

              {approvedUsers.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-gray-400">
                  승인된 사용자가 없습니다.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          이름 (사번)
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          소속
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          역할
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          Pod TTL
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          승인일
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          작업
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200 bg-white">
                      {approvedUsers.map((u) => (
                        <tr key={u.id} className="hover:bg-gray-50">
                          <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                            {u.name ?? u.username}
                            <span className="ml-1 text-xs text-gray-400">({u.username})</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                            {[u.region_name, u.team_name, u.job_name].filter(Boolean).join(" / ") || "-"}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            {roleBadge(u.role)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <select
                              value={u.pod_ttl}
                              onChange={(e) =>
                                handleTtlChange(u.id, e.target.value)
                              }
                              className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              {TTL_OPTIONS.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                            {formatDate(u.approved_at)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <button
                              onClick={() => handleRevoke(u.id, u.username)}
                              className="rounded bg-red-50 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-100 transition-colors"
                            >
                              승인취소
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
