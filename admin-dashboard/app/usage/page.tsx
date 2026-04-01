"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  getTokenUsage,
  getTokenUsageDaily,
  getTokenUsageMonthly,
  getTokenUsageHourly,
  getTokenUsageDailyTrend,
  getTokenUsageMonthlyTrend,
  takeTokenSnapshot,
  type TokenUsageResponse,
  type DailyUsageResponse,
  type MonthlyUsageResponse,
  type HourlyUsageResponse,
  type DailyTrendItem,
  type MonthlyTrendItem,
  type DailyUsageUser,
  getUserUsageHistory,
  type UserUsageHistory,
} from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import StatsCard from "@/components/stats-card";
import Pagination, { SearchInput } from "@/components/pagination";

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

function Sparkline({ data, width = 140, height = 32 }: { data: number[]; width?: number; height?: number }) {
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);

  if (!data || data.length === 0 || data.every(v => v === 0)) {
    return <div style={{ width, height }} className="text-gray-300 text-xs flex items-center">—</div>;
  }
  const max = Math.max(...data) || 1;
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - (v / max) * (height - 6) - 3;
    return `${x},${y}`;
  }).join(" ");

  // Current UTC hour for masking future data points
  const nowUtcH = new Date().getUTCHours();

  return (
    <div className="relative inline-block" style={{ width, height }}>
      <svg width={width} height={height} onMouseLeave={() => setHover(null)}>
        <polyline points={points} fill="none" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        {data.map((v, i) => {
          const x = (i / (data.length - 1)) * width;
          const y = height - (v / max) * (height - 6) - 3;
          return (
            <g key={i}>
              <circle cx={x} cy={y} r="6" fill="transparent"
                onMouseEnter={() => setHover({ i, x, y })} />
              {i <= nowUtcH && v > 0 && (
                <circle cx={x} cy={y} r="1.5" fill="#3b82f6" opacity="0.5" />
              )}
              {hover?.i === i && (
                <circle cx={x} cy={y} r="3" fill="#3b82f6" />
              )}
            </g>
          );
        })}
      </svg>
      {hover && (() => {
        const kstH = (hover.i + 9) % 24;
        // UTC 0-14 → KST same date (yesterday from user's KST "today")
        // UTC 15-23 → KST next date (today in KST)
        const isYesterday = hover.i + 9 < 24;
        return (
          <div
            className="absolute z-10 rounded bg-gray-900 px-2 py-1 text-xs text-white shadow-lg whitespace-nowrap pointer-events-none"
            style={{ left: Math.min(hover.x, width - 100), top: -28 }}
          >
            {String(kstH).padStart(2, "0")}시{isYesterday ? " (전일)" : ""} — {data[hover.i].toLocaleString()} tokens
          </div>
        );
      })()}
    </div>
  );
}

type TrendItem = (DailyTrendItem | MonthlyTrendItem) & Record<string, unknown>;

function TrendChart({ data, label, dateKey = "date", periodLabel }: {
  data: (DailyTrendItem | MonthlyTrendItem)[];
  label: string;
  dateKey?: string;
  periodLabel?: string;
}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const items = data as any[];
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  if (!items || items.length === 0) {
    return <div className="flex items-center justify-center py-8 text-gray-400 text-sm">추이 데이터가 없습니다</div>;
  }

  const maxTokens = Math.max(...items.map((d: any) => d.total_tokens)) || 1;
  const chartW = 700;
  const chartH = 120;
  const barW = Math.max(12, (chartW - items.length * 4) / items.length);
  const totalTokens = items.reduce((s: number, d: any) => s + d.total_tokens, 0);
  const totalCostKrw = items.reduce((s: number, d: any) => s + d.cost_krw, 0);

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm mb-6">
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-gray-900">{label} ({periodLabel ?? `${items.length}일`})</h2>
        <div className="flex gap-4 text-xs text-gray-500">
          <span>총 토큰: <strong className="text-gray-900">{fmt(totalTokens)}</strong></span>
          <span>총 비용: <strong className="text-gray-900">{fmt(totalCostKrw)}원</strong></span>
        </div>
      </div>
      <div className="px-4 py-3 overflow-x-auto">
        <div className="relative" style={{ width: chartW, height: chartH + 24 }} onMouseLeave={() => setHoverIdx(null)}>
          <svg width={chartW} height={chartH}>
            {items.map((d: any, i: number) => {
              const barH = Math.max(2, (d.total_tokens / maxTokens) * (chartH - 8));
              const x = i * (barW + 4);
              const y = chartH - barH;
              const isHover = hoverIdx === i;
              return (
                <g key={String(d[dateKey])} onMouseEnter={() => setHoverIdx(i)}>
                  <rect x={x} y={y} width={barW} height={barH} rx={3}
                    fill={isHover ? "#2563eb" : "#93c5fd"} className="transition-colors" />
                </g>
              );
            })}
          </svg>
          <div className="flex mt-1" style={{ width: chartW }}>
            {items.map((d: any) => (
              <span key={String(d[dateKey])} className="text-[10px] text-gray-400 text-center" style={{ width: barW + 4 }}>
                {String(d[dateKey]).slice(dateKey === "month" ? 2 : 5)}
              </span>
            ))}
          </div>
          {hoverIdx !== null && items[hoverIdx] && (
            <div
              className="absolute z-10 rounded-lg bg-gray-900 px-3 py-2 text-xs text-white shadow-lg pointer-events-none"
              style={{
                left: Math.min(hoverIdx * (barW + 4), chartW - 180),
                top: -8,
              }}
            >
              <div className="font-medium">{String(items[hoverIdx][dateKey])}</div>
              <div>토큰: {fmt(items[hoverIdx].total_tokens)} (in {fmt(items[hoverIdx].input_tokens)} / out {fmt(items[hoverIdx].output_tokens)})</div>
              <div>비용: ${items[hoverIdx].cost_usd.toFixed(2)} / {fmt(items[hoverIdx].cost_krw)}원</div>
              <div>활성 사용자: {items[hoverIdx].active_users}명</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function UsagePage() {
  const router = useRouter();

  const [tab, setTab] = useState<Tab>("realtime");
  const [selectedDate, setSelectedDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [selectedMonth, setSelectedMonth] = useState(() => new Date().toISOString().slice(0, 7));

  const [realtimeData, setRealtimeData] = useState<TokenUsageResponse | null>(null);
  const [dailyData, setDailyData] = useState<DailyUsageResponse | null>(null);
  const [monthlyData, setMonthlyData] = useState<MonthlyUsageResponse | null>(null);
  const [hourlyData, setHourlyData] = useState<HourlyUsageResponse | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapshotMsg, setSnapshotMsg] = useState("");

  // 개인별 일별 추이
  const [detailUser, setDetailUser] = useState<string | null>(null);
  const [detailUserName, setDetailUserName] = useState("");
  const [detailFrom, setDetailFrom] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [detailTo, setDetailTo] = useState(() => new Date().toISOString().slice(0, 10));
  const [detailHistory, setDetailHistory] = useState<UserUsageHistory[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);

  // Company-wide trends
  const [dailyTrendData, setDailyTrendData] = useState<DailyTrendItem[]>([]);
  const [monthlyTrendData, setMonthlyTrendData] = useState<MonthlyTrendItem[]>([]);

  // Pagination & search
  const [usageSearch, setUsageSearch] = useState("");
  const [usagePage, setUsagePage] = useState(1);
  const [detailPage, setDetailPage] = useState(1);
  const PAGE_SIZE = 10;

  const fetchRealtime = useCallback(async () => {
    try {
      const [res, hourly] = await Promise.all([
        getTokenUsage(),
        getTokenUsageHourly().catch(() => null),
      ]);
      setRealtimeData(res);
      if (hourly) setHourlyData(hourly);
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

  const fetchUserDetail = useCallback(async (username: string) => {
    setDetailLoading(true);
    try {
      const res = await getUserUsageHistory(username, detailFrom, detailTo);
      // Filter by date range
      setDetailHistory(
        (res.history || []).filter((h) => h.date >= detailFrom && h.date <= detailTo)
      );
    } catch {
      setDetailHistory([]);
    } finally {
      setDetailLoading(false);
    }
  }, [detailFrom, detailTo]);

  // Fetch detail when user/dates change
  useEffect(() => {
    if (detailUser) fetchUserDetail(detailUser);
  }, [detailUser, detailFrom, detailTo, fetchUserDetail]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    if (tab === "realtime") fetchRealtime();
    else if (tab === "daily") fetchDaily();
    else fetchMonthly();

    // Fetch company-wide trends
    if (tab === "daily") {
      getTokenUsageDailyTrend(30).then(res => setDailyTrendData(res.trend)).catch(() => {});
    }
    if (tab === "monthly") {
      getTokenUsageMonthlyTrend("2026-03").then(res => setMonthlyTrendData(res.trend)).catch(() => {});
    }
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

  // Filter and paginate users
  const filteredUsers = (users || []).filter((u) => {
    if (!usageSearch) return true;
    const q = usageSearch.toLowerCase();
    return (u.user_name ?? u.username).toLowerCase().includes(q) ||
      u.username.toLowerCase().includes(q);
  });

  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => { setUsagePage(1); }, [usageSearch, tab]);
  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => { setDetailPage(1); }, [detailUser]);

  const usageTotalPages = Math.max(1, Math.ceil(filteredUsers.length / PAGE_SIZE));
  const usageSafePage = Math.min(usagePage, usageTotalPages);
  const paginatedUsers = filteredUsers.slice((usageSafePage - 1) * PAGE_SIZE, usageSafePage * PAGE_SIZE);

  // Paginate detail history
  const detailTotalPages = Math.max(1, Math.ceil(detailHistory.length / PAGE_SIZE));
  const detailSafePage = Math.min(detailPage, detailTotalPages);
  const paginatedDetail = detailHistory.slice((detailSafePage - 1) * PAGE_SIZE, detailSafePage * PAGE_SIZE);

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
              <Link href="/apps" className="hover:text-gray-900 transition-colors">
                앱 관리
              </Link>
              <Link href="/audit" className="hover:text-gray-900 transition-colors">
                감사 로그
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

        {/* Company-wide trend charts */}
        {tab === "daily" && dailyTrendData.length > 0 && (
          <TrendChart data={dailyTrendData} dateKey="date" label="전사 일별 토큰 사용량 추이" periodLabel={`최근 ${dailyTrendData.length}일`} />
        )}
        {tab === "monthly" && monthlyTrendData.length > 0 && (
          <TrendChart data={monthlyTrendData} dateKey="month" label="전사 월별 토큰 사용량 추이" periodLabel={`${monthlyTrendData.length}개월`} />
        )}

        {/* User table */}
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-900">사용자별 토큰 사용량</h2>
              <p className="mt-0.5 text-xs text-gray-400">
              {tab === "realtime" && realtimeData && (
                <>수집: {new Date(realtimeData.collected_at).toLocaleString("ko-KR")} / 30초 자동 갱신</>
              )}
              {tab === "daily" && dailyData && <>날짜: {dailyData.date}</>}
              {tab === "monthly" && monthlyData && <>월: {monthlyData.month}</>}
            </p>
            </div>
            <div className="flex items-center gap-2">
              <SearchInput value={usageSearch} onChange={setUsageSearch} placeholder="사용자 검색..." />
              {usageSearch && <span className="text-xs text-gray-400">{filteredUsers.length}건</span>}
            </div>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-gray-400">
              데이터를 불러오는 중...
            </div>
          ) : (
            <>
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
                    {tab === "realtime" && (
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">시간별 추이</th>
                    )}
                    {showSessionCols && (
                      <>
                        <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">사용시간</th>
                        <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">최종 사용</th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {paginatedUsers.map((u) => (
                    <tr key={u.username} className={`hover:bg-gray-50 cursor-pointer ${detailUser === u.username ? "bg-blue-50" : ""}`}
                      onClick={() => { setDetailUser(u.username); setDetailUserName(u.user_name || u.username); }}>
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
                      {tab === "realtime" && (
                        <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                          <Sparkline data={hourlyData?.users?.[u.username] || []} />
                        </td>
                      )}
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
                {totals && filteredUsers.length > 0 && (
                  <tfoot className="bg-gray-50">
                    <tr className="font-semibold">
                      <td className="px-4 py-3 text-sm text-gray-900">합계 ({filteredUsers.length}명)</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_input)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_output)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_tokens)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">${totals.total_cost_usd.toFixed(4)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(totals.total_cost_krw)}원</td>
                      {tab === "realtime" && (
                        <td className="px-4 py-3 text-sm tabular-nums" />
                      )}
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
            <Pagination
              currentPage={usageSafePage}
              totalPages={usageTotalPages}
              totalItems={filteredUsers.length}
              itemsPerPage={PAGE_SIZE}
              onPageChange={setUsagePage}
            />
            </>
          )}
        </div>

        {/* 개인별 일별 추이 패널 */}
        {detailUser && (
          <div className="mt-6 rounded-lg border border-blue-200 bg-white shadow-sm">
            <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
              <h2 className="text-sm font-semibold text-gray-900">
                {detailUserName} ({detailUser}) 일별 추이
              </h2>
              <div className="flex items-center gap-3">
                <label className="text-xs text-gray-500">시작:</label>
                <input type="date" value={detailFrom}
                  onChange={(e) => setDetailFrom(e.target.value)}
                  className="rounded border border-gray-300 px-2 py-1 text-sm" />
                <label className="text-xs text-gray-500">종료:</label>
                <input type="date" value={detailTo}
                  onChange={(e) => setDetailTo(e.target.value)}
                  className="rounded border border-gray-300 px-2 py-1 text-sm" />
                <button onClick={() => setDetailUser(null)}
                  className="text-gray-400 hover:text-gray-600 text-sm">✕ 닫기</button>
              </div>
            </div>
            {detailLoading ? (
              <div className="flex items-center justify-center py-8 text-gray-400">불러오는 중...</div>
            ) : detailHistory.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-gray-400">해당 기간에 데이터가 없습니다</div>
            ) : (
              <>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">날짜</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Input</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Output</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">Total</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">USD</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">KRW</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">사용시간</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {paginatedDetail.map((h) => (
                      <tr key={h.date} className="hover:bg-gray-50">
                        <td className="whitespace-nowrap px-4 py-2 text-sm text-gray-900">{h.date}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-gray-600">{fmt(h.input_tokens)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-gray-600">{fmt(h.output_tokens)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums font-medium text-gray-900">{fmt(h.total_tokens)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-gray-600">${h.cost_usd.toFixed(4)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-gray-600">{fmt(h.cost_krw)}원</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-gray-600">{formatMinutes(h.session_minutes)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot className="bg-gray-50">
                    <tr className="font-semibold">
                      <td className="px-4 py-2 text-sm">합계 ({detailHistory.length}일)</td>
                      <td className="px-4 py-2 text-right text-sm tabular-nums">{fmt(detailHistory.reduce((s, h) => s + h.input_tokens, 0))}</td>
                      <td className="px-4 py-2 text-right text-sm tabular-nums">{fmt(detailHistory.reduce((s, h) => s + h.output_tokens, 0))}</td>
                      <td className="px-4 py-2 text-right text-sm tabular-nums">{fmt(detailHistory.reduce((s, h) => s + h.total_tokens, 0))}</td>
                      <td className="px-4 py-2 text-right text-sm tabular-nums">${detailHistory.reduce((s, h) => s + h.cost_usd, 0).toFixed(4)}</td>
                      <td className="px-4 py-2 text-right text-sm tabular-nums">{fmt(detailHistory.reduce((s, h) => s + h.cost_krw, 0))}원</td>
                      <td className="px-4 py-2 text-right text-sm tabular-nums">{formatMinutes(detailHistory.reduce((s, h) => s + h.session_minutes, 0))}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <Pagination
                currentPage={detailSafePage}
                totalPages={detailTotalPages}
                totalItems={detailHistory.length}
                itemsPerPage={PAGE_SIZE}
                onPageChange={setDetailPage}
              />
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
