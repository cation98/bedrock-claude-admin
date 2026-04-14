"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { getMaintenanceStatus, setMaintenanceMode, type MaintenanceStatus } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

function formatKST(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

function toLocalDatetimeInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function MaintenancePage() {
  const router = useRouter();
  const [status, setStatus] = useState<MaintenanceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // 폼 상태
  const [title, setTitle] = useState("서비스 점검 중");
  const [description, setDescription] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");

  const fetchStatus = useCallback(async () => {
    try {
      const s = await getMaintenanceStatus();
      setStatus(s);
      setTitle(s.title || "서비스 점검 중");
      setDescription(s.description || "");
      setStartTime(toLocalDatetimeInput(s.start_time));
      setEndTime(toLocalDatetimeInput(s.end_time));
    } catch (e) {
      setError(e instanceof Error ? e.message : "불러오기 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) { router.replace("/"); return; }
    fetchStatus();
  }, [router, fetchStatus]);

  const handleToggle = async (active: boolean) => {
    if (active && !confirm("점검 모드를 활성화하면 서비스 전체 접근이 차단됩니다.\n관리자 경로(/admin)는 계속 사용 가능합니다.\n\n계속하시겠습니까?")) return;
    setSaving(true);
    setError("");
    try {
      await setMaintenanceMode({
        is_active: active,
        title,
        description,
        start_time: startTime ? new Date(startTime).toISOString() : null,
        end_time: endTime ? new Date(endTime).toISOString() : null,
      });
      setSuccess(active ? "점검 모드 활성화됨 — claude.skons.net에 점검 페이지가 표시됩니다." : "점검 모드 해제됨 — 서비스가 정상 운영됩니다.");
      fetchStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      await setMaintenanceMode({
        is_active: status?.is_active ?? false,
        title,
        description,
        start_time: startTime ? new Date(startTime).toISOString() : null,
        end_time: endTime ? new Date(endTime).toISOString() : null,
      });
      setSuccess("점검 내용 저장됨");
      fetchStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const isActive = status?.is_active ?? false;

  return (
    <main className="mx-auto max-w-2xl px-4 py-8 sm:px-6">
      {error && <div className="mb-4 rounded-md bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">{error}</div>}
      {success && <div className="mb-4 rounded-md bg-[var(--success-light)] px-4 py-3 text-sm text-[var(--success)]">{success}</div>}

      {/* 현재 상태 배너 */}
      <div className={`mb-6 rounded-xl border-2 p-5 ${isActive ? "border-[var(--danger)] bg-[var(--danger-light)]/20" : "border-[var(--border)] bg-[var(--surface)]"}`}>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-[var(--text-primary)]">서비스 점검 모드</h1>
            <p className="mt-0.5 text-sm text-[var(--text-muted)]">
              {isActive
                ? "현재 활성화 — claude.skons.net에 점검 페이지 표시 중"
                : "현재 비활성화 — 서비스 정상 운영 중"}
            </p>
            {status?.updated_by && (
              <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                마지막 수정: {status.updated_by} · {formatKST(status.updated_at)}
              </p>
            )}
          </div>
          <div className={`rounded-full px-4 py-1.5 text-sm font-semibold ${isActive ? "bg-[var(--danger)] text-white" : "bg-[var(--success-light)] text-[var(--success)]"}`}>
            {isActive ? "🔧 점검 중" : "✅ 정상"}
          </div>
        </div>
      </div>

      {loading ? (
        <div className="text-center text-sm text-[var(--text-muted)]">불러오는 중...</div>
      ) : (
        <>
          {/* 점검 내용 편집 */}
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-5 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold text-[var(--text-primary)]">점검 내용 설정</h2>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-secondary)]">점검 제목</label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="예: 서버 점검 중"
                  className="w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-secondary)]">점검 내용</label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={4}
                  placeholder="점검 작업 내용을 입력하세요."
                  className="w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-[var(--text-secondary)]">시작 시간</label>
                  <input
                    type="datetime-local"
                    value={startTime}
                    onChange={(e) => setStartTime(e.target.value)}
                    className="w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-[var(--text-secondary)]">완료 예정</label>
                  <input
                    type="datetime-local"
                    value={endTime}
                    onChange={(e) => setEndTime(e.target.value)}
                    className="w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
              </div>
            </div>

            <div className="mt-4 flex justify-end">
              <button
                disabled={saving}
                onClick={handleSave}
                className="rounded-md border border-[var(--border)] px-4 py-1.5 text-sm font-medium text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] disabled:opacity-50"
              >
                {saving ? "저장 중..." : "내용만 저장"}
              </button>
            </div>
          </div>

          {/* 점검 모드 ON/OFF */}
          <div className="mt-4 grid grid-cols-2 gap-3">
            <button
              disabled={saving || isActive}
              onClick={() => handleToggle(true)}
              className="rounded-lg bg-[var(--danger)] py-3 text-sm font-bold text-white hover:opacity-90 disabled:opacity-40"
            >
              🔧 점검 모드 활성화
            </button>
            <button
              disabled={saving || !isActive}
              onClick={() => handleToggle(false)}
              className="rounded-lg bg-[var(--success)] py-3 text-sm font-bold text-white hover:opacity-90 disabled:opacity-40"
            >
              ✅ 점검 해제 (서비스 재개)
            </button>
          </div>

          {/* 점검 페이지 미리보기 링크 */}
          <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 py-3 text-xs text-[var(--text-muted)]">
            <span className="font-medium text-[var(--text-secondary)]">점검 모드 적용 대상</span>
            {" "}— claude.skons.net 전체 경로 (단, <span className="font-mono">/api/v1/admin</span> 관리자 경로 제외)
          </div>
        </>
      )}
    </main>
  );
}
