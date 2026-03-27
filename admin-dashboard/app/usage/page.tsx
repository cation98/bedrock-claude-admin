"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { getTokenUsage, type TokenUsageResponse } from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import StatsCard from "@/components/stats-card";

const REFRESH_INTERVAL = 30_000;

function fmt(n: number): string {
  return n.toLocaleString();
}

export default function UsagePage() {
  const router = useRouter();
  const [data, setData] = useState<TokenUsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const res = await getTokenUsage();
      setData(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
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

        {data && (
          <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-5">
            <StatsCard label="총 토큰" value={fmt(data.total_tokens)} />
            <StatsCard label="Input" value={fmt(data.total_input)} />
            <StatsCard label="Output" value={fmt(data.total_output)} />
            <StatsCard label="비용 (USD)" value={`$${data.total_cost_usd.toFixed(2)}`} />
            <StatsCard label="비용 (KRW)" value={`${fmt(data.total_cost_krw)}원`} />
          </div>
        )}

        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-900">사용자별 토큰 사용량</h2>
            {data && (
              <p className="mt-0.5 text-xs text-gray-400">
                수집: {new Date(data.collected_at).toLocaleString("ko-KR")} / 30초 자동 갱신
              </p>
            )}
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
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {data?.users.map((u) => (
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
                    </tr>
                  ))}
                </tbody>
                {data && data.users.length > 0 && (
                  <tfoot className="bg-gray-50">
                    <tr className="font-semibold">
                      <td className="px-4 py-3 text-sm text-gray-900">합계 ({data.users.length}명)</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(data.total_input)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(data.total_output)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(data.total_tokens)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">${data.total_cost_usd.toFixed(4)}</td>
                      <td className="px-4 py-3 text-right text-sm tabular-nums">{fmt(data.total_cost_krw)}원</td>
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
