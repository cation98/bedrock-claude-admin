"use client";

import type { DepartmentAnalysisData } from "@/lib/api";

function cellColor(value: number, max: number): string {
  if (max === 0) return "bg-[var(--surface)]";
  const ratio = value / max;
  if (ratio === 0) return "bg-[var(--surface)]";
  if (ratio < 0.2) return "bg-indigo-900/30";
  if (ratio < 0.4) return "bg-indigo-800/50";
  if (ratio < 0.6) return "bg-indigo-700/60";
  if (ratio < 0.8) return "bg-indigo-600/75";
  return "bg-indigo-500/90";
}

interface Props {
  data: DepartmentAnalysisData;
}

export default function DepartmentHeatmap({ data }: Props) {
  const { departments, nodes } = data;

  if (nodes.length === 0 || departments.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-[var(--text-muted)]">
        부서별 스냅샷 데이터가 없습니다.
      </div>
    );
  }

  const allValues = nodes.flatMap((n) => Object.values(n.by_department));
  const maxValue = Math.max(...allValues, 1);

  return (
    <div className="overflow-x-auto">
      <table className="min-w-max text-xs">
        <thead>
          <tr>
            <th className="sticky left-0 min-w-[140px] bg-[var(--background)] px-3 py-2 text-left text-[var(--text-muted)]">
              개념
            </th>
            {departments.map((d) => (
              <th key={d} className="min-w-[80px] px-2 py-2 text-center text-[var(--text-muted)]">
                {d}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {nodes.slice(0, 50).map((node) => (
            <tr key={node.node_id} className="border-t border-[var(--border)]">
              <td className="sticky left-0 bg-[var(--background)] px-3 py-1.5 text-[var(--text-primary)]">
                <span>{node.concept_name}</span>
                <span className="ml-1 text-[10px] text-[var(--text-muted)]">
                  ({node.concept_type})
                </span>
              </td>
              {departments.map((d) => {
                const val = node.by_department[d] || 0;
                return (
                  <td
                    key={d}
                    title={`${node.concept_name} / ${d}: ${val}회`}
                    className={`px-2 py-1.5 text-center ${cellColor(val, maxValue)}`}
                  >
                    {val > 0 ? (
                      <span className="text-[var(--text-primary)]">{val}</span>
                    ) : (
                      <span className="text-[var(--text-muted)]">—</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {nodes.length > 50 && (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          상위 50개 개념만 표시 (전체 {nodes.length}개)
        </p>
      )}
    </div>
  );
}
