"use client";

import type { Session } from "@/lib/api";

interface SessionTableProps {
  sessions: Session[];
  onTerminate?: (podName: string) => void;
  loading?: boolean;
}

function statusBadge(status: string) {
  const base = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";

  switch (status) {
    case "running":
      return <span className={`${base} bg-green-100 text-green-700`}>{status}</span>;
    case "creating":
      return <span className={`${base} bg-yellow-100 text-yellow-700`}>{status}</span>;
    case "terminated":
      return <span className={`${base} bg-gray-100 text-gray-500`}>{status}</span>;
    default:
      return <span className={`${base} bg-gray-100 text-gray-500`}>{status}</span>;
  }
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function SessionTable({
  sessions,
  onTerminate,
  loading,
}: SessionTableProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-gray-400">
        데이터를 불러오는 중...
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-gray-400">
        활성 세션이 없습니다.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              사용자
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              Pod 이름
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              상태
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              세션 유형
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              시작 시간
            </th>
            {onTerminate && (
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                작업
              </th>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 bg-white">
          {sessions.map((s) => (
            <tr key={s.pod_name} className="hover:bg-gray-50">
              <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                {s.username}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600 font-mono">
                {s.pod_name}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm">
                {statusBadge(s.status)}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                {s.session_type}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                {formatDate(s.started_at)}
              </td>
              {onTerminate && (
                <td className="whitespace-nowrap px-4 py-3 text-sm">
                  {s.status !== "terminated" && (
                    <button
                      onClick={() => onTerminate(s.pod_name)}
                      className="rounded bg-red-50 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-100 transition-colors"
                    >
                      종료
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
