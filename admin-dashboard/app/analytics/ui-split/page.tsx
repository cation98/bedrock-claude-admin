"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  fetchUiSplitStats,
  type UiSplitSummary,
  type UiSplitBucket,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

function fmt(n: number): string {
  return n.toLocaleString();
}

function pct(numerator: number, denominator: number): string {
  if (denominator === 0) return "0%";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

function SummaryCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
      <p className="text-sm font-medium text-[var(--text-muted)]">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-[var(--text-primary)]">{value}</p>
      {sub && <p className="mt-1 text-xs text-[var(--text-muted)]">{sub}</p>}
    </div>
  );
}

function BucketBar({ bucket }: { bucket: UiSplitBucket }) {
  const total = bucket.webchat_users + bucket.console_users;
  const webchatPct = total === 0 ? 0 : (bucket.webchat_users / total) * 100;
  const consolePct = total === 0 ? 0 : (bucket.console_users / total) * 100;

  return (
    <div className="flex h-4 w-full overflow-hidden rounded-sm">
      {webchatPct > 0 && (
        <div
          title={`Webchat: ${bucket.webchat_users}명 (${webchatPct.toFixed(1)}%)`}
          style={{ width: `${webchatPct}%` }}
          className="bg-[var(--primary)] opacity-80"
        />
      )}
      {consolePct > 0 && (
        <div
          title={`Console: ${bucket.console_users}명 (${consolePct.toFixed(1)}%)`}
          style={{ width: `${consolePct}%` }}
          className="bg-[var(--success)] opacity-80"
        />
      )}
      {total === 0 && (
        <div className="w-full bg-[var(--border)]" />
      )}
    </div>
  );
}

export default function UiSplitPage() {
  const router = useRouter();
  const [period, setPeriod] = useState<"weekly" | "monthly">("weekly");
  const [window, setWindow] = useState<number>(8);
  const [windowInput, setWindowInput] = useState<string>("8");
  const [data, setData] = useState<UiSplitSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await fetchUiSplitStats(period, window);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [period, window]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    fetchData();
  }, [router, fetchData]);

  function handleWindowChange(e: React.ChangeEvent<HTMLInputElement>) {
    setWindowInput(e.target.value);
    const v = parseInt(e.target.value, 10);
    if (!isNaN(v) && v >= 4 && v <= 52) {
      setWindow(v);
    }
  }

  const totalUsers =
    data
      ? data.webchat_total_users + data.console_total_users - data.both_users
      : 0;

  return (
    <div className="p-6 space-y-6">
      {/* 헤더 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">UI 사용률 분석</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Webchat과 Console 사용 패턴을 기간별로 비교합니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Period 토글 */}
          <div className="flex rounded-md border border-[var(--border-strong)] overflow-hidden">
            <button
              onClick={() => setPeriod("weekly")}
              className={`px-3 py-1.5 text-xs transition-colors ${
                period === "weekly"
                  ? "bg-[var(--primary)] text-white"
                  : "bg-[var(--surface)] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
              }`}
            >
              주간
            </button>
            <button
              onClick={() => setPeriod("monthly")}
              className={`px-3 py-1.5 text-xs transition-colors ${
                period === "monthly"
                  ? "bg-[var(--primary)] text-white"
                  : "bg-[var(--surface)] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
              }`}
            >
              월간
            </button>
          </div>
          {/* Window 입력 */}
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-[var(--text-muted)]">기간</label>
            <input
              type="number"
              min={4}
              max={52}
              value={windowInput}
              onChange={handleWindowChange}
              className="w-14 rounded-md border border-[var(--border-strong)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
            />
            <span className="text-xs text-[var(--text-muted)]">
              {period === "weekly" ? "주" : "개월"}
            </span>
          </div>
          {/* 새로고침 */}
          <button
            onClick={fetchData}
            disabled={loading}
            className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] disabled:opacity-40"
          >
            {loading ? "로딩 중..." : "새로고침"}
          </button>
        </div>
      </div>

      {/* 오류 */}
      {error && (
        <div className="rounded-md border border-[var(--danger)] bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {/* 요약 카드 */}
      {!loading && data && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          <SummaryCard
            label="총 사용자"
            value={fmt(totalUsers)}
            sub="Webchat + Console (중복 제거)"
          />
          <SummaryCard
            label="Webchat only"
            value={fmt(data.webchat_only_users)}
            sub={`전체의 ${pct(data.webchat_only_users, totalUsers)}`}
          />
          <SummaryCard
            label="Console only"
            value={fmt(data.console_only_users)}
            sub={`전체의 ${pct(data.console_only_users, totalUsers)}`}
          />
          <SummaryCard
            label="둘 다 사용"
            value={fmt(data.both_users)}
            sub={`전체의 ${pct(data.both_users, totalUsers)}`}
          />
          <SummaryCard
            label="겹침 비율"
            value={pct(data.both_users, totalUsers)}
            sub="Webchat ∩ Console"
          />
        </div>
      )}

      {/* 로딩 스켈레톤 */}
      {loading && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-24 rounded-lg border border-[var(--border)] bg-[var(--surface)] animate-pulse" />
          ))}
        </div>
      )}

      {/* 버킷 테이블 + Bar Chart */}
      <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
        <div className="flex items-center gap-4 border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-base font-semibold text-[var(--text-primary)]">
            기간별 분포
          </h2>
          {/* 범례 */}
          <div className="ml-auto flex items-center gap-4 text-xs text-[var(--text-muted)]">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-3 w-3 rounded-sm bg-[var(--primary)] opacity-80" />
              Webchat
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-3 w-3 rounded-sm bg-[var(--success)] opacity-80" />
              Console
            </span>
          </div>
        </div>

        {loading ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
            로딩 중...
          </div>
        ) : !data || data.buckets.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
            데이터가 없습니다.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-xs text-[var(--text-muted)]">
                  <th className="px-4 py-2 text-left font-medium">시작일</th>
                  <th className="px-4 py-2 text-left font-medium">종료일</th>
                  <th className="px-4 py-2 text-right font-medium">Webchat</th>
                  <th className="px-4 py-2 text-right font-medium">Console</th>
                  <th className="px-4 py-2 text-right font-medium">이벤트</th>
                  <th className="px-4 py-2 text-left font-medium w-40">비율</th>
                </tr>
              </thead>
              <tbody>
                {data.buckets.map((bucket, idx) => (
                  <tr
                    key={idx}
                    className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]"
                  >
                    <td className="px-4 py-2.5 text-[var(--text-secondary)] font-mono text-xs">
                      {bucket.period_start}
                    </td>
                    <td className="px-4 py-2.5 text-[var(--text-secondary)] font-mono text-xs">
                      {bucket.period_end}
                    </td>
                    <td className="px-4 py-2.5 text-right text-[var(--text-primary)]">
                      {fmt(bucket.webchat_users)}
                    </td>
                    <td className="px-4 py-2.5 text-right text-[var(--text-primary)]">
                      {fmt(bucket.console_users)}
                    </td>
                    <td className="px-4 py-2.5 text-right text-[var(--text-muted)]">
                      {fmt(bucket.total_events)}
                    </td>
                    <td className="px-4 py-2.5 w-40">
                      <BucketBar bucket={bucket} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
