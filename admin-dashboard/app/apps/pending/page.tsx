"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getPendingApps,
  approveApp,
  rejectApp,
  PendingApp,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

function authModeBadge(mode: string) {
  const base =
    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (mode) {
    case "none":
      return (
        <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>
          없음
        </span>
      );
    case "password":
      return (
        <span className={`${base} bg-[var(--warning-light)] text-[var(--warning)]`}>
          비밀번호
        </span>
      );
    case "custom_2fa":
      return (
        <span className={`${base} bg-[var(--info-light)] text-[var(--info)]`}>
          2FA
        </span>
      );
    default:
      return (
        <span className={`${base} bg-[var(--primary-light)] text-[var(--primary)]`}>
          {mode}
        </span>
      );
  }
}

function visibilityBadge(v: string) {
  const base =
    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  if (v === "public") {
    return (
      <span className={`${base} bg-[var(--success-light)] text-[var(--success)]`}>
        공개
      </span>
    );
  }
  return (
    <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>
      {v}
    </span>
  );
}

export default function PendingAppsPage() {
  const router = useRouter();
  const [apps, setApps] = useState<PendingApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [processing, setProcessing] = useState<Record<number, boolean>>({});

  const fetchData = useCallback(async () => {
    try {
      const res = await getPendingApps();
      setApps(res.apps);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "목록 조회 실패");
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
  }, [router, fetchData]);

  async function handleApprove(app: PendingApp) {
    if (!confirm(`"${app.app_name}" 앱을 승인하시겠습니까?`)) return;
    setProcessing((p) => ({ ...p, [app.id]: true }));
    try {
      await approveApp(app.id);
      await fetchData();
    } catch (err) {
      alert(err instanceof Error ? err.message : "승인 처리 실패");
    } finally {
      setProcessing((p) => ({ ...p, [app.id]: false }));
    }
  }

  async function handleReject(app: PendingApp) {
    const reason = prompt("거절 사유를 입력하세요 (10자 이상):");
    if (reason === null) return;
    if (reason.trim().length < 10) {
      alert("거절 사유는 10자 이상이어야 합니다.");
      return;
    }
    setProcessing((p) => ({ ...p, [app.id]: true }));
    try {
      await rejectApp(app.id, reason.trim());
      await fetchData();
    } catch (err) {
      alert(err instanceof Error ? err.message : "거절 처리 실패");
    } finally {
      setProcessing((p) => ({ ...p, [app.id]: false }));
    }
  }

  function formatDate(s: string | null) {
    if (!s) return "-";
    return new Date(s).toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-[var(--text-primary)]">배포 승인</h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          승인 대기 중인 앱을 검토하고 승인 또는 거절하세요.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-[var(--danger)] bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
        <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-base font-semibold text-[var(--text-primary)]">
            승인 대기 앱
            {!loading && (
              <span className="ml-2 rounded-full bg-[var(--warning-light)] px-2 py-0.5 text-xs font-medium text-[var(--warning)]">
                {apps.length}건
              </span>
            )}
          </h2>
          <button
            onClick={fetchData}
            disabled={loading}
            className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] disabled:opacity-40"
          >
            새로고침
          </button>
        </div>

        {loading ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
            로딩 중...
          </div>
        ) : apps.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
            승인 대기 앱이 없습니다.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-[var(--border)]">
              <thead className="bg-[var(--bg)]">
                <tr>
                  {[
                    "사번",
                    "이름",
                    "팀",
                    "앱명",
                    "버전",
                    "공개 범위",
                    "인증",
                    "2FA 확인",
                    "배포 시각",
                    "액션",
                  ].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                {apps.map((app) => (
                  <tr key={app.id} className="hover:bg-[var(--bg)]">
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-mono text-[var(--text-secondary)]">
                      {app.owner_username}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-primary)]">
                      {app.owner_name ?? "-"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                      {app.owner_team ?? "-"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                      {app.app_name}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-muted)]">
                      {app.version}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      {visibilityBadge(app.visibility)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      {authModeBadge(app.auth_mode)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      {app.custom_2fa_attested ? (
                        <span className="text-[var(--success)]">✓</span>
                      ) : (
                        <span className="text-[var(--text-muted)]">-</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-muted)]">
                      {formatDate(app.created_at)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleApprove(app)}
                          disabled={!!processing[app.id]}
                          className="rounded-md bg-[var(--primary)] px-3 py-1 text-xs font-medium text-white hover:bg-[var(--primary-hover)] disabled:opacity-40"
                        >
                          승인
                        </button>
                        <button
                          onClick={() => handleReject(app)}
                          disabled={!!processing[app.id]}
                          className="rounded-md border border-[var(--danger)] px-3 py-1 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] disabled:opacity-40"
                        >
                          거절
                        </button>
                      </div>
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
