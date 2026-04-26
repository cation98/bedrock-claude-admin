"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getTokenUsage,
  getTokenUsageDaily,
  getTokenUsageMonthly,
  getTokenUsageHourly,
  getTokenUsageDailyTrend,
  getTokenUsageMonthlyTrend,
  takeTokenSnapshot,
  getPricingTable,
  type TokenUsageResponse,
  type DailyUsageResponse,
  type MonthlyUsageResponse,
  type HourlyUsageResponse,
  type DailyTrendItem,
  type MonthlyTrendItem,
  type DailyUsageUser,
  getUserUsageHistory,
  type UserUsageHistory,
  type PricingResponse,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
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

// 144 slots (10-min resolution). UTC slot → KST time.
// UTC date's data spans two KST days:
//   slots 0-89 (UTC 00:00-14:50) → KST 09:00-23:50 (same KST date)
//   slots 90-143 (UTC 15:00-23:50) → KST 00:00-08:50 (next KST date)
function slotToKst(utcSlot: number): { h: number; m: number; nextDay: boolean } {
  const utcMin = utcSlot * 10;
  const kstMin = utcMin + 9 * 60;
  const nextDay = kstMin >= 24 * 60;
  const totalMin = kstMin % (24 * 60);
  return { h: Math.floor(totalMin / 60), m: totalMin % 60, nextDay };
}

function Sparkline({ data, width = 360, height = 32 }: { data: number[]; width?: number; height?: number }) {
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);

  if (!data || data.length === 0 || data.every(v => v === 0)) {
    return <div style={{ width, height }} className="text-[var(--text-muted)] text-xs flex items-center">—</div>;
  }

  const len = data.length;

  // Current UTC slot — only render up to here (no future empty slots)
  const now = new Date();
  const nowSlot = len >= 144
    ? now.getUTCHours() * 6 + Math.floor(now.getUTCMinutes() / 10)
    : now.getUTCHours();

  // Trim data to current slot + 1
  const visibleEnd = Math.min(nowSlot + 1, len);
  const visibleRaw = data.slice(0, visibleEnd);

  // Convert cumulative → incremental (per-slot delta)
  const visibleData = visibleRaw.map((v, i) => {
    if (i === 0) return 0;
    const delta = v - visibleRaw[i - 1];
    return delta > 0 ? delta : 0;
  });
  const visLen = visibleData.length;

  if (visLen === 0 || visibleData.every(v => v === 0)) {
    return <div style={{ width, height }} className="text-[var(--text-muted)] text-xs flex items-center">—</div>;
  }

  const max = Math.max(...visibleData) || 1;

  // KST midnight in UTC slots
  const midnightSlot = len >= 144 ? 90 : 15;
  const midnightX = midnightSlot < visLen ? (midnightSlot / (visLen - 1)) * width : -1;

  // Build polyline points for visible range only
  const points = visibleData.map((v, i) => {
    const x = visLen > 1 ? (i / (visLen - 1)) * width : width / 2;
    const y = height - (v / max) * (height - 6) - 3;
    return { x, y, v, i };
  });

  const yesterdayPts = points.filter(p => p.i < midnightSlot).map(p => `${p.x},${p.y}`);
  const todayPts = points.filter(p => p.i >= midnightSlot).map(p => `${p.x},${p.y}`);

  // Hit areas: only at slots with data or every N slots for hover
  const step = 1; // every slot is hoverable (10-min resolution)

  return (
    <div className="relative inline-block" style={{ width, height }}>
      <svg width={width} height={height} onMouseLeave={() => setHover(null)}>
        {midnightX >= 0 && (
          <line x1={midnightX} y1={0} x2={midnightX} y2={height}
            stroke="#d1d5db" strokeWidth="1" strokeDasharray="2,2" />
        )}
        {yesterdayPts.length > 1 && (
          <polyline points={yesterdayPts.join(" ")} fill="none" stroke="#93c5fd" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.5" />
        )}
        {todayPts.length > 1 && (
          <polyline points={todayPts.join(" ")} fill="none" stroke="#2563eb" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        )}
        {/* Hit areas for hover */}
        {points.filter((_, i) => i % step === 0 || data[i] > 0).map(p => {
          const isToday = p.i >= midnightSlot;
          return (
            <g key={p.i}>
              <circle cx={p.x} cy={p.y} r={len >= 144 ? 3 : 6} fill="transparent"
                onMouseEnter={() => setHover({ i: p.i, x: p.x, y: p.y })} />
              {p.v > 0 && (
                <circle cx={p.x} cy={p.y} r="1.5" fill={isToday ? "#2563eb" : "#93c5fd"} opacity={isToday ? 0.7 : 0.4} />
              )}
              {hover?.i === p.i && (
                <circle cx={p.x} cy={p.y} r="3" fill={isToday ? "#2563eb" : "#93c5fd"} />
              )}
            </g>
          );
        })}
      </svg>
      {hover && (() => {
        const kst = visLen >= 144 || len >= 144
          ? slotToKst(hover.i)
          : { h: (hover.i + 9) % 24, m: 0, nextDay: hover.i + 9 >= 24 };
        return (
          <div
            className="absolute z-10 rounded bg-[var(--text-primary)] px-2 py-1 text-xs text-white shadow-lg whitespace-nowrap pointer-events-none"
            style={{ left: Math.min(hover.x, width - 120), top: -28 }}
          >
            {String(kst.h).padStart(2, "0")}:{String(kst.m).padStart(2, "0")}{kst.nextDay ? " (+1)" : ""} — +{(visibleData[hover.i] ?? 0).toLocaleString()} (누적 {(visibleRaw[hover.i] ?? 0).toLocaleString()})
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
    return <div className="flex items-center justify-center py-8 text-[var(--text-muted)] text-sm">추이 데이터가 없습니다</div>;
  }

  const maxTokens = Math.max(...items.map((d: any) => d.total_tokens)) || 1;
  const chartW = 700;
  const chartH = 120;
  const barW = Math.max(12, (chartW - items.length * 4) / items.length);
  const totalTokens = items.reduce((s: number, d: any) => s + d.total_tokens, 0);
  const totalCostKrw = items.reduce((s: number, d: any) => s + d.cost_krw, 0);

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm mb-6">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-sm font-semibold text-[var(--text-primary)]">{label} ({periodLabel ?? `${items.length}일`})</h2>
        <div className="flex gap-4 text-xs text-[var(--text-muted)]">
          <span>총 토큰: <strong className="text-[var(--text-primary)]">{fmt(totalTokens)}</strong></span>
          <span>총 비용: <strong className="text-[var(--text-primary)]">{fmt(totalCostKrw)}원</strong></span>
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
              <span key={String(d[dateKey])} className="text-[10px] text-[var(--text-muted)] text-center" style={{ width: barW + 4 }}>
                {String(d[dateKey]).slice(dateKey === "month" ? 2 : 5)}
              </span>
            ))}
          </div>
          {hoverIdx !== null && items[hoverIdx] && (
            <div
              className="absolute z-10 rounded-lg bg-[var(--text-primary)] px-3 py-2 text-xs text-white shadow-lg pointer-events-none"
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
  const [pricing, setPricing] = useState<PricingResponse | null>(null);

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

    // Pricing table (fetch once)
    if (!pricing) {
      getPricingTable().then(setPricing).catch(() => {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const tabBtnClass = (t: Tab) =>
    `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      tab === t
        ? "bg-[var(--primary)] text-white"
        : "bg-[var(--surface)] text-[var(--text-secondary)] border border-[var(--border-strong)] hover:bg-[var(--bg)]"
    }`;

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-[var(--error-light)] px-4 py-3 text-sm text-[var(--error)]">{error}</div>
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
                className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
              />
            )}
            {tab === "monthly" && (
              <input
                type="month"
                value={selectedMonth}
                onChange={(e) => setSelectedMonth(e.target.value)}
                className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
              />
            )}
            <button
              onClick={handleSnapshot}
              className="rounded-md border border-[var(--primary)] bg-[var(--primary-light)] px-3 py-1.5 text-sm font-medium text-[var(--primary)] hover:bg-[var(--primary-light)] transition-colors"
            >
              스냅샷 저장
            </button>
          </div>
        </div>

        {snapshotMsg && (
          <div className="mb-4 rounded-md bg-[var(--success-light)] px-4 py-2 text-sm text-[var(--success)]">
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
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">사용자별 토큰 사용량</h2>
              <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {tab === "realtime" && realtimeData && (
                <>수집: {new Date(realtimeData.collected_at).toLocaleString("ko-KR")} / 30초 자동 갱신</>
              )}
              {tab === "daily" && dailyData && <>날짜: {dailyData.date}</>}
              {tab === "monthly" && monthlyData && <>월: {monthlyData.month}</>}
            </p>
            </div>
            <div className="flex items-center gap-2">
              <SearchInput value={usageSearch} onChange={setUsageSearch} placeholder="사용자 검색..." />
              {usageSearch && <span className="text-xs text-[var(--text-muted)]">{filteredUsers.length}건</span>}
            </div>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
              데이터를 불러오는 중...
            </div>
          ) : (
            <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-[var(--border)]">
                <thead className="bg-[var(--bg)]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">사용자</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Input</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Output</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Total</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">USD</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">KRW</th>
                    {tab === "realtime" && (
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">시간별 추이</th>
                    )}
                    {showSessionCols && (
                      <>
                        <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">사용시간</th>
                        <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">최종 사용</th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {paginatedUsers.map((u) => (
                    <tr key={u.username} className={`hover:bg-[var(--bg)] cursor-pointer ${detailUser === u.username ? "bg-[var(--primary-light)]" : ""}`}
                      onClick={() => { setDetailUser(u.username); setDetailUserName(u.user_name || u.username); }}>
                      <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                        {u.user_name ?? u.username}
                        <span className="ml-1 text-xs text-[var(--text-muted)]">({u.username})</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        {fmt(u.input_tokens)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        {fmt(u.output_tokens)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums font-medium text-[var(--text-primary)]">
                        {fmt(u.total_tokens)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        ${u.cost_usd.toFixed(4)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        {fmt(u.cost_krw)}원
                      </td>
                      {tab === "realtime" && (
                        <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                          <Sparkline data={hourlyData?.users?.[u.username] || []} />
                        </td>
                      )}
                      {showSessionCols && (
                        <>
                          <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                            {formatMinutes(u.session_minutes)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                            {formatTime(u.last_activity_at)}
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
                {totals && filteredUsers.length > 0 && (
                  <tfoot className="bg-[var(--bg)]">
                    <tr className="font-semibold">
                      <td className="px-4 py-3 text-sm text-[var(--text-primary)]">합계 ({filteredUsers.length}명)</td>
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
          <div className="mt-6 rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                {detailUserName} ({detailUser}) 일별 추이
              </h2>
              <div className="flex items-center gap-3">
                <label className="text-xs text-[var(--text-muted)]">시작:</label>
                <input type="date" value={detailFrom}
                  onChange={(e) => setDetailFrom(e.target.value)}
                  className="rounded border border-[var(--border-strong)] px-2 py-1 text-sm" />
                <label className="text-xs text-[var(--text-muted)]">종료:</label>
                <input type="date" value={detailTo}
                  onChange={(e) => setDetailTo(e.target.value)}
                  className="rounded border border-[var(--border-strong)] px-2 py-1 text-sm" />
                <button onClick={() => setDetailUser(null)}
                  className="text-[var(--text-muted)] hover:text-[var(--text-secondary)] text-sm">✕ 닫기</button>
              </div>
            </div>
            {detailLoading ? (
              <div className="flex items-center justify-center py-8 text-[var(--text-muted)]">불러오는 중...</div>
            ) : detailHistory.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-[var(--text-muted)]">해당 기간에 데이터가 없습니다</div>
            ) : (
              <>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-[var(--border)]">
                  <thead className="bg-[var(--bg)]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">날짜</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Input</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Output</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Total</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">USD</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">KRW</th>
                      <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">사용시간</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {paginatedDetail.map((h) => (
                      <tr key={h.date} className="hover:bg-[var(--bg)]">
                        <td className="whitespace-nowrap px-4 py-2 text-sm text-[var(--text-primary)]">{h.date}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-[var(--text-secondary)]">{fmt(h.input_tokens)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-[var(--text-secondary)]">{fmt(h.output_tokens)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums font-medium text-[var(--text-primary)]">{fmt(h.total_tokens)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-[var(--text-secondary)]">${h.cost_usd.toFixed(4)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-[var(--text-secondary)]">{fmt(h.cost_krw)}원</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right text-sm tabular-nums text-[var(--text-secondary)]">{formatMinutes(h.session_minutes)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot className="bg-[var(--bg)]">
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
        {/* Pricing Reference */}
        {pricing && (
          <div className="mt-6 rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">Bedrock 모델 가격표</h2>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                  {pricing.unit} · 기준일 {pricing.as_of} · 1 USD = {pricing.krw_rate.toLocaleString()}원
                </p>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-[var(--border)]">
                <thead className="bg-[var(--bg)]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-[var(--text-muted)]">모델</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Input (USD)</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Output (USD)</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Cache Write</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Cache Read</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Input (KRW)</th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase text-[var(--text-muted)]">Output (KRW)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {pricing.models.map((m) => (
                    <tr key={m.model_id} className="hover:bg-[var(--bg)]">
                      <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                        {m.display_name}
                        <span className="ml-2 text-xs text-[var(--text-muted)] font-normal">{m.model_id}</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        ${m.input_usd.toFixed(2)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        ${m.output_usd.toFixed(2)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-muted)]">
                        ${m.cache_creation_usd.toFixed(2)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-muted)]">
                        ${m.cache_read_usd.toFixed(2)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        {m.input_krw.toLocaleString()}원
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm tabular-nums text-[var(--text-secondary)]">
                        {m.output_krw.toLocaleString()}원
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </>
  );
}
