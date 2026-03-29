"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  getTokenUsage,
  getTokenUsageDaily,
  getTokenUsageMonthly,
  takeTokenSnapshot,
  type TokenUsageResponse,
  type DailyUsageResponse,
  type MonthlyUsageResponse,
  type DailyUsageUser,
} from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import StatsCard from "@/components/stats-card";

const REFRESH_INTERVAL = 30_000;

type Tab = "realtime" | "daily" | "monthly";

function fmt(n: number): string {
  return n.toLocaleString();
}

function formatMinutes(m: number): string {
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem > 0 ? `${h}시간 ${rem}분` : `${h}시간`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function UsagePage() {
  const router = useRouter();

  const [tab, setTab] = useState<Tab>("realtime");
  const [selectedDate, setSelectedDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [selectedMonth, setSelectedMonth] = useState(() => new Date().toISOString().slice(0, 7));

  const [realtimeData, setRealtimeData] = useState<TokenUsageResponse | null>(null);
  const [dailyData, setDailyData] = useState<DailyUsageResponse | null>(null);
  const [monthlyData, setMonthlyData] = useState<MonthlyUsageResponse | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapshotMsg, setSnapshotMsg] = useState("");

  const fetchRealtime = useCallback(async () => {
    try {
      const res = await getTokenUsage();
      setRealtimeData(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchDaily = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getTokenUsageDaily(selectedDate);
      setDailyData(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, [selectedDate]);

  const fetchMonthly = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getTokenUsageMonthly(selectedMonth);
      setMonthlyData(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, [selectedMonth]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    if (tab === "realtime") fetchRealtime();
    else if (tab === "daily") fetchDaily();
    else fetchMonthly();
  }, [tab, selectedDate, selectedMonth, router, fetchRealtime, fetchDaily, fetchMonthly]);

  // Auto-refresh only for realtime tab
  useEffect(() => {
    if (tab !== "realtime") return;
    const timer = setInterval(fetchRealtime, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [tab, fetchRealtime]);

  const handleSnapshot = async () => {
    setSnapshotMsg("");
    try {
      const res = await takeTokenSnapshot();
      setSnapshotMsg(`${res.date} 스냅샷 저장 완료 (${res.saved}명)`);
    } catch (err) {
      setSnapshotMsg(err instanceof Error ? err.message : "스냅샷 저장 실패");
    }
  };

  // Unified totals for stats cards
  const totals = (() => {
    if (tab === "realtime" && realtimeData) {
      return {
        total_tokens: realtimeData.total_tokens,
        total_input: realtimeData.total_input,
        total_output: realtimeData.total_output,
        total_cost_usd: realtimeData.total_cost_usd,
        total_cost_krw: realtimeData.total_cost_krw,
      };
    }
    if (tab === "daily" && dailyData) {
      return {
        total_tokens: dailyData.total_tokens,
        total_input: dailyData.total_input,
        total_output: dailyData.total_output,
        total_cost_usd: dailyData.total_cost_usd,
        total_cost_krw: dailyData.total_cost_krw,
      };
    }
    if (tab === "monthly" && monthlyData) {
      const users = monthlyData.users || [];
      return {
        total_tokens: users.reduce((s, u) => s + (u.total_tokens || 0), 0),
        total_input: users.reduce((s, u) => s + (u.input_tokens || 0), 0),
        total_output: users.reduce((s, u) => s + (u.output_tokens || 0), 0),
        total_cost_usd: users.reduce((s, u) => s + (u.cost_usd || 0), 0),
        total_cost_krw: users.reduce((s, u) => s + (u.cost_krw || 0), 0),
      };
    }
    return null;
  })();

  // Unified user rows
  const users: DailyUsageUser[] | null = (() => {
    if (tab === "realtime" && realtimeData) {
      return realtimeData.users.map((u) => ({
        ...u,
        session_minutes: 0,
        last_activity_at: null,
      }));
    }
    if (tab === "daily" && dailyData) return dailyData.users;
    if (tab === "monthly" && monthlyData) return monthlyData.users;
    return null;
  })();

  const showSessionCols = tab !== "realtime";

  const user = getUser();

  const tabBtnClass = (t: Tab) =>
    `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      tab === t
        ? "bg-blue-600 text-white"
        : "bg-white text-gray-600 border border-gray-300 hover:bg-gray-50"
    }`;

  return (
    <div className="min-h-screen bg-gray-50/50">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
          <h1 className="text-lg font-bold text-gray-900">Claude Code Admin</h1>
          <div className="flex items-center gap-6">
            <nav className="flex gap-4 text-sm font-medium text-gray-600">
              <Link href="/dashboard" className="hover:text-gray-900 transition-colors">
                운용현황
              </Link>
              <Link href="/users" className="hover:text-gray-900 transition-colors">
                사용자 관리
              </Link>
              <Link href="/security" className="hover:text-gray-900 transition-colors">
                보안 정책
              </Link>
              <Link href="/usage" className="text-blue-600 border-b-2 border-blue-600 pb-0.5">
                토큰 사용량
              </Link>
              <Link href="/infra" className="hover:text-gray-900 transition-colors">
                인프라
              </Link>
            </nav>
            <div className="flex items-center gap-3">
              {user && <span className="text-sm text-gray-500">{user.name}</span>}
              <button
                onClick={logout}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
              >
                로그아웃
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}

        {/* Toolbar: tabs + date picker + snapshot */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <div className="flex gap-2">
            <button className={tabBtnClass("realtime")} onClick={() => setTab("realtime")}>
              실시간
            </button>
            <button className={tabBtnClass("daily")} onClick={() => setTab("daily")}>
              일별
            </button>
            <button className={tabBtnClass("monthly")} onClick={() => setTab("monthly")}>
              월별
            </button>
          </div>

          <div className="ml-auto flex items-center gap-3">
            {tab === "daily" && (
              <input
                type="date"
                value={selectedDate}
                onChange={(e) => setSelectedDate(e.target.value)}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            )}
            {tab === "monthly" && (
              <input
                type="month"
                value={selectedMonth}
                onChange={(e) => setSelectedMonth(e.target.value)}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            )}
            <button
              onClick={handleSnapshot}
              className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-100 transition-colors"
            >
              스냅샷 저장
            </button>
          </div>
        </div>

        {snapshotMsg && (
          <div className="mb-4 rounded-md bg-green-50 px-4 py-2 text-sm text-green-700">
            {snapshotMsg}
          </div>
        )}

        {/* Stats cards */}
        {totals && (
          <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-5">
            <StatsCard label="총 토큰" value={fmt(totals.total_tokens)} />
            <StatsCard label="Input" value={fmt(totals.total_input)} />
            <StatsCard label="Output" value={fmt(totals.total_output)} />
            <StatsCard label="비용 (USD)" value={`$${totals.total_cost_usd.toFixed(2)}`} />
            <StatsCard label="비용 (KRW)" value={`${fmt(totals.total_cost_krw)}원`} />
          </div>
        )}

        {/* User table */}
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-900">사용자별 토큰 사용량</h2>
            <p className="mt-0.5 text-xs text-gray-400">
              {tab === "realtime" && realtimeData && (
                <>수집: {new Date(realtimeData.collected_at).toLocaleString("ko-KR")} / 30초 자동 갱신</>
              )}
              {tab === "daily" && dailyData && <>날짜: {dailyData.date}</>}
              {tab === "monthly" && monthlyData && <>월: {monthlyData.month}</>}
            </p>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-gray-400">
              데이터를 불러오는 중...
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">사용자</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Input</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Output</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Total</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">USD</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">KRW</th>
                    {showSessionCols && (
                      <>
                        <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">사용시간</th>
                        <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">최종 사용</th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {users?.map((u) => (
                    <tr key={u.username} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                        {u.user_name ?? u.username}
                        <span className="ml-1 text-xs text-gray-400">({u.username})</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                        {fmt(u.input_tokens)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                        {fmt(u.output_tokens)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums font-medium text-gray-900">
                        {fmt(u.total_tokens)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                        ${u.cost_usd.toFixed(4)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                        {fmt(u.cost_krw)}원
                      </td>
                      {showSessionCols && (
                        <>
                          <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                            {formatMinutes(u.session_minutes)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                            {formatTime(u.last_activity_at)}
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
                {totals && users && users.length > 0 && (
                  <tfoot className="bg-gray-50">
                    <tr className="font-semibold">
                      <td className="px-4 py-3 text-sm text-gray-900">합계 ({users.length}명)</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_input)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_output)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_tokens)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">${totals.total_cost_usd.toFixed(4)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_cost_krw)}원</td>
                      {showSessionCols && (
                        <>
                          <td className="px-4 py-3 text-right text-sm tabular-nums" />
                          <td className="px-4 py-3 text-right text-sm tabular-nums" />
                        </>
                      )}
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
