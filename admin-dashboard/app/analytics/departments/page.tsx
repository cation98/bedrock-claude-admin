"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { fetchDepartmentAnalysis, type DepartmentAnalysisData } from "@/lib/api";

const DepartmentHeatmap = dynamic(() => import("@/components/DepartmentHeatmap"), { ssr: false });

const PERIOD_OPTIONS = [
  { value: "daily", label: "일별" },
  { value: "weekly", label: "주별" },
  { value: "monthly", label: "월별" },
];

export default function DepartmentsPage() {
  const [data, setData] = useState<DepartmentAnalysisData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<"daily" | "weekly" | "monthly">("monthly");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchDepartmentAnalysis(period));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "오류 발생");
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">부서 지식 분포</h1>
          <p className="text-sm text-[var(--text-muted)]">부서별 암묵지 히트맵</p>
        </div>
        <div className="flex gap-2">
          {PERIOD_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setPeriod(opt.value as typeof period)}
              className={`rounded px-3 py-1 text-sm ${
                period === opt.value
                  ? "bg-indigo-600 text-white"
                  : "bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)]"
              }`}
            >
              {opt.label}
            </button>
          ))}
          <button
            onClick={load}
            disabled={loading}
            className="rounded bg-[var(--surface)] px-3 py-1 text-sm text-[var(--text-muted)] hover:bg-[var(--surface-hover)] disabled:opacity-50"
          >
            {loading ? "로딩..." : "새로고침"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
      )}

      {data && !loading && <DepartmentHeatmap data={data} />}

      {data && data.nodes.length === 0 && !loading && (
        <div className="rounded bg-[var(--surface)] p-8 text-center text-[var(--text-muted)]">
          부서별 스냅샷 데이터가 없습니다.
          <br />
          <span className="text-xs">knowledge_snapshots.department_breakdown에 데이터가 채워지면 표시됩니다.</span>
        </div>
      )}
    </div>
  );
}
