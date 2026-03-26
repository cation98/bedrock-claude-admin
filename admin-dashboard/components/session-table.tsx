"use client";

import { useEffect, useState } from "react";
import type { Session } from "@/lib/api";
import FileModal from "./file-modal";

interface SessionTableProps {
  sessions: Session[];
  onTerminate?: (sessionId: number) => void;
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

function formatCountdown(expiresAt: string | null): { text: string; expired: boolean } {
  if (!expiresAt) {
    return { text: "만료없음", expired: false };
  }

  const now = Date.now();
  const expires = new Date(expiresAt).getTime();
  const remaining = expires - now;

  if (remaining <= 0) {
    return { text: "만료됨", expired: true };
  }

  const totalMinutes = Math.floor(remaining / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0) {
    return { text: `${hours}h ${minutes}m`, expired: false };
  }
  return { text: `${minutes}m`, expired: false };
}

export default function SessionTable({
  sessions,
  onTerminate,
  loading,
}: SessionTableProps) {
  const [fileModalPod, setFileModalPod] = useState<Session | null>(null);
  const [, setTick] = useState(0);

  const hasExpiry = sessions.some((s) => s.expires_at != null);
  useEffect(() => {
    if (!hasExpiry) return;
    const timer = setInterval(() => setTick((t) => t + 1), 1_000);
    return () => clearInterval(timer);
  }, [hasExpiry]);
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
    <>
    {fileModalPod && (
      <FileModal
        podName={fileModalPod.pod_name}
        username={fileModalPod.username}
        onClose={() => setFileModalPod(null)}
      />
    )}
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
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              접속
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              남은시간
            </th>
            {onTerminate && (
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                관리
              </th>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 bg-white">
          {sessions.map((s) => {
            const countdown = formatCountdown(s.expires_at);
            return (
              <tr key={s.pod_name} className="hover:bg-gray-50">
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                  {s.user_name ?? s.username}
                  <span className="ml-1 text-xs text-gray-400">({s.username})</span>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600 font-mono">
                  {s.pod_name}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm">
                  {statusBadge(s.pod_status)}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                  {s.session_type}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                  {formatDate(s.started_at)}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm">
                  {s.pod_status === "running" && (
                    <div className="flex gap-1.5">
                      <a
                        href={s.terminal_url ?? `/terminal/${s.pod_name}/`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded bg-blue-50 px-3 py-1 text-xs font-medium text-blue-600 hover:bg-blue-100 transition-colors"
                      >
                        터미널
                      </a>
                      <button
                        onClick={() => setFileModalPod(s)}
                        className="rounded bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-600 hover:bg-emerald-100 transition-colors"
                      >
                        파일
                      </button>
                    </div>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm">
                  <span
                    className={
                      countdown.expired
                        ? "font-medium text-red-600"
                        : countdown.text === "만료없음"
                          ? "text-gray-400"
                          : "text-gray-600"
                    }
                  >
                    {countdown.text}
                  </span>
                </td>
                {onTerminate && (
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    {s.pod_status !== "terminated" && (
                      <button
                        onClick={() => onTerminate(s.id)}
                        className="rounded bg-red-50 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-100 transition-colors"
                      >
                        종료
                      </button>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </>
  );
}
