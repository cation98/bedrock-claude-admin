"use client";

import { useEffect, useState } from "react";
import type { Session } from "@/lib/api";
import FileModal from "./file-modal";
import Pagination, { SearchInput } from "./pagination";

interface SessionTableProps {
  sessions: Session[];
  onTerminate?: (sessionId: number) => void;
  loading?: boolean;
}

function statusBadge(status: string) {
  const base = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";

  switch (status) {
    case "running":
      return <span className={`${base} bg-[var(--success-light)] text-[var(--success)]`}>{status}</span>;
    case "creating":
      return <span className={`${base} bg-[var(--warning-light)] text-[var(--warning)]`}>{status}</span>;
    case "terminated":
      return <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>{status}</span>;
    default:
      return <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>{status}</span>;
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
  const [sessionSearch, setSessionSearch] = useState("");
  const [sessionPage, setSessionPage] = useState(1);
  const PAGE_SIZE = 10;

  const filteredSessions = sessions.filter((s) => {
    if (!sessionSearch) return true;
    const q = sessionSearch.toLowerCase();
    return (s.user_name ?? s.username).toLowerCase().includes(q) ||
      s.username.toLowerCase().includes(q) ||
      s.pod_name.toLowerCase().includes(q);
  });

  useEffect(() => { setSessionPage(1); }, [sessionSearch]);

  const totalPages = Math.max(1, Math.ceil(filteredSessions.length / PAGE_SIZE));
  const safePage = Math.min(sessionPage, totalPages);
  const paginatedSessions = filteredSessions.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const hasExpiry = sessions.some((s) => s.expires_at != null);
  useEffect(() => {
    if (!hasExpiry) return;
    const timer = setInterval(() => setTick((t) => t + 1), 1_000);
    return () => clearInterval(timer);
  }, [hasExpiry]);
  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
        데이터를 불러오는 중...
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
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
    <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--border)]">
      <SearchInput value={sessionSearch} onChange={setSessionSearch} placeholder="세션 검색 (사용자, Pod)..." />
      {sessionSearch && <span className="text-xs text-[var(--text-muted)]">{filteredSessions.length}건</span>}
    </div>
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-[var(--border)]">
        <thead className="bg-[var(--bg)]">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              사용자
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              Pod 이름
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              상태
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              운용 유형
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              시작 시간
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              접속
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              남은시간
            </th>
            {onTerminate && (
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                관리
              </th>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
          {paginatedSessions.map((s) => {
            const countdown = formatCountdown(s.expires_at);
            return (
              <tr key={s.pod_name} className="hover:bg-[var(--bg)]">
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                  {s.user_name ?? s.username}
                  <span className="ml-1 text-xs text-[var(--text-muted)]">({s.username})</span>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)] font-mono">
                  {s.pod_name}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm">
                  {statusBadge(s.pod_status)}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                  {{"unlimited":"만료없음","weekday-office":"평일 09-18시","30d":"30일","7d":"7일","1d":"1일","8h":"8시간","4h":"4시간"}[s.session_type] ?? s.session_type}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                  {formatDate(s.started_at)}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm">
                  {s.pod_status === "running" && (
                    <div className="flex gap-1.5">
                      <a
                        href={`https://claude.skons.net/terminal/${s.pod_name}/`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded bg-[var(--primary-light)] px-3 py-1 text-xs font-medium text-[var(--primary)] hover:bg-[var(--primary-light)] transition-colors"
                      >
                        터미널
                      </a>
                      <button
                        onClick={() => setFileModalPod(s)}
                        className="rounded bg-[var(--success-light)] px-3 py-1 text-xs font-medium text-[var(--success)] hover:bg-[var(--success-light)] transition-colors"
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
                        ? "font-medium text-[var(--danger)]"
                        : countdown.text === "만료없음"
                          ? "text-[var(--text-muted)]"
                          : "text-[var(--text-secondary)]"
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
                        className="rounded bg-[var(--danger-light)] px-3 py-1 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] transition-colors"
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
    <Pagination
      currentPage={safePage}
      totalPages={totalPages}
      totalItems={filteredSessions.length}
      itemsPerPage={PAGE_SIZE}
      onPageChange={setSessionPage}
    />
    </>
  );
}
