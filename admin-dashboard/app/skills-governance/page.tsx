"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchPendingSkillsProgress, type SkillPendingProgressItem } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import SkillApprovalCard from "@/components/SkillApprovalCard";

interface Toast {
  type: "success" | "error";
  message: string;
}

export default function SkillsGovernancePage() {
  const router = useRouter();
  const [skills, setSkills] = useState<SkillPendingProgressItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(type: "success" | "error", message: string) {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast({ type, message });
    toastTimerRef.current = setTimeout(() => setToast(null), 4000);
  }

  const fetchData = useCallback(async () => {
    try {
      const items = await fetchPendingSkillsProgress();
      setSkills(items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "목록 조회에 실패했습니다.");
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

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, []);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">스킬 거버넌스</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">승인 대기 중인 스킬을 검토하고 승인 또는 반려하세요.</p>
        </div>
        <button
          onClick={fetchData}
          disabled={loading}
          className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] disabled:opacity-40"
        >
          새로고침
        </button>
      </div>

      {/* Toast */}
      {toast && (
        <div
          className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm transition-all ${
            toast.type === "success"
              ? "border-[var(--success)] bg-[var(--success-light)] text-[var(--success)]"
              : "border-[var(--danger)] bg-[var(--danger-light)] text-[var(--danger)]"
          }`}
        >
          <span>{toast.type === "success" ? "✓" : "✕"}</span>
          <span>{toast.message}</span>
          <button
            onClick={() => setToast(null)}
            className="ml-auto text-current opacity-60 hover:opacity-100"
          >
            ✕
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-md border border-[var(--danger)] bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {/* Card List */}
      <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
        {/* Section header */}
        <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-base font-semibold text-[var(--text-primary)]">
            승인 대기 스킬
            {!loading && (
              <span className="ml-2 rounded-full bg-[var(--warning-light)] px-2 py-0.5 text-xs font-medium text-[var(--warning)]">
                {skills.length}건
              </span>
            )}
          </h2>
        </div>

        {loading ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
            로딩 중...
          </div>
        ) : skills.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
            승인 대기 스킬이 없습니다.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {skills.map((item) => (
              <SkillApprovalCard
                key={item.skill_id}
                item={item}
                onRefresh={fetchData}
                onToast={showToast}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
