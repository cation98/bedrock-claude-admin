"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  fetchPendingSkillsProgress,
  fetchApprovedSkillsAdmin,
  fetchRejectedSkills,
  revokeSkill,
  fetchGovernanceEvents,
  type SkillPendingProgressItem,
  type SkillAdminItem,
  type SkillRejectedItem,
  type GovernanceEvent,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import SkillApprovalCard from "@/components/SkillApprovalCard";

type Tab = "pending" | "approved" | "rejected" | "audit";

interface Toast {
  type: "success" | "error";
  message: string;
}

const TAB_LABELS: Record<Tab, string> = {
  pending: "승인 대기",
  approved: "승인됨",
  rejected: "반려됨",
  audit: "감사 로그",
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  submit: "제출",
  approve: "승인",
  reject: "반려",
  delete: "삭제",
  version_bump: "버전 업",
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  submit: "bg-blue-50 text-blue-700 border-blue-200",
  approve: "bg-green-50 text-green-700 border-green-200",
  reject: "bg-red-50 text-red-700 border-red-200",
  delete: "bg-gray-50 text-gray-600 border-gray-200",
  version_bump: "bg-yellow-50 text-yellow-700 border-yellow-200",
};

export default function SkillsGovernancePage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<Tab>("pending");
  const [pendingSkills, setPendingSkills] = useState<SkillPendingProgressItem[]>([]);
  const [approvedSkills, setApprovedSkills] = useState<SkillAdminItem[]>([]);
  const [rejectedSkills, setRejectedSkills] = useState<SkillRejectedItem[]>([]);
  const [events, setEvents] = useState<GovernanceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);
  const [revokeConfirm, setRevokeConfirm] = useState<number | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadedTabsRef = useRef<Set<Tab>>(new Set());

  function showToast(type: "success" | "error", message: string) {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast({ type, message });
    toastTimerRef.current = setTimeout(() => setToast(null), 4000);
  }

  const loadTab = useCallback(async (tab: Tab, force = false) => {
    if (!force && loadedTabsRef.current.has(tab)) return;
    setLoading(true);
    setError("");
    try {
      if (tab === "pending") {
        const items = await fetchPendingSkillsProgress();
        setPendingSkills(items);
      } else if (tab === "approved") {
        const data = await fetchApprovedSkillsAdmin();
        setApprovedSkills(data.skills);
      } else if (tab === "rejected") {
        const items = await fetchRejectedSkills();
        setRejectedSkills(items);
      } else {
        const items = await fetchGovernanceEvents();
        setEvents(items);
      }
      loadedTabsRef.current.add(tab);
    } catch (err) {
      setError(err instanceof Error ? err.message : "조회에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    loadTab("pending");
  }, [router, loadTab]);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, []);

  const handleTabChange = useCallback(
    (tab: Tab) => {
      setActiveTab(tab);
      setError("");
      loadTab(tab);
    },
    [loadTab]
  );

  const handleRefresh = useCallback(() => {
    loadedTabsRef.current.delete(activeTab);
    loadTab(activeTab, true);
  }, [activeTab, loadTab]);

  // Called by SkillApprovalCard after approve/reject — invalidates related tabs
  const handleSkillAction = useCallback(() => {
    loadedTabsRef.current.delete("approved");
    loadedTabsRef.current.delete("rejected");
    loadedTabsRef.current.delete("audit");
    loadTab("pending", true);
  }, [loadTab]);

  const handleRevoke = useCallback(
    async (skillId: number) => {
      try {
        await revokeSkill(skillId);
        showToast("success", "스킬이 삭제되었습니다.");
        setApprovedSkills((prev) => prev.filter((s) => s.id !== skillId));
        loadedTabsRef.current.delete("audit");
        setRevokeConfirm(null);
      } catch (err) {
        showToast("error", err instanceof Error ? err.message : "삭제에 실패했습니다.");
        setRevokeConfirm(null);
      }
    },
    []
  );

  const tabCounts: Partial<Record<Tab, number>> = {
    pending: pendingSkills.length,
    approved: approvedSkills.length,
    rejected: rejectedSkills.length,
  };

  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">스킬 거버넌스</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            사용자가 제출한 스킬을 검토하고 승인·반려·삭제하세요.
          </p>
        </div>
        <button
          onClick={handleRefresh}
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
            className="ml-auto opacity-60 hover:opacity-100"
          >
            ✕
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-[var(--border)]">
        <nav className="-mb-px flex gap-1">
          {(["pending", "approved", "rejected", "audit"] as Tab[]).map((tab) => {
            const count = tabCounts[tab];
            const isActive = activeTab === tab;
            return (
              <button
                key={tab}
                onClick={() => handleTabChange(tab)}
                className={`inline-flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "border-[var(--accent)] text-[var(--accent)]"
                    : "border-transparent text-[var(--text-secondary)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
                }`}
              >
                {TAB_LABELS[tab]}
                {count !== undefined && count > 0 && (
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-xs font-medium ${
                      tab === "pending"
                        ? "bg-[var(--warning-light)] text-[var(--warning)]"
                        : "bg-[var(--surface-2)] text-[var(--text-muted)]"
                    }`}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-md border border-[var(--danger)] bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {/* Tab Content */}
      <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">

        {/* ── Pending ── */}
        {activeTab === "pending" && (
          <>
            <div className="border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">
                승인 대기 스킬
                {!loading && pendingSkills.length > 0 && (
                  <span className="ml-2 rounded-full bg-[var(--warning-light)] px-2 py-0.5 text-xs font-medium text-[var(--warning)]">
                    {pendingSkills.length}건
                  </span>
                )}
              </h2>
            </div>
            {loading ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">로딩 중...</div>
            ) : pendingSkills.length === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">
                승인 대기 스킬이 없습니다.
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
                {pendingSkills.map((item) => (
                  <SkillApprovalCard
                    key={item.skill_id}
                    item={item}
                    onRefresh={handleSkillAction}
                    onToast={showToast}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Approved ── */}
        {activeTab === "approved" && (
          <>
            <div className="border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">
                승인된 스킬
                {!loading && approvedSkills.length > 0 && (
                  <span className="ml-2 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-xs font-medium text-[var(--text-muted)]">
                    {approvedSkills.length}건
                  </span>
                )}
              </h2>
            </div>
            {loading ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">로딩 중...</div>
            ) : approvedSkills.length === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">
                승인된 스킬이 없습니다.
              </div>
            ) : (
              <div className="divide-y divide-[var(--border)]">
                {approvedSkills.map((skill) => (
                  <div
                    key={skill.id}
                    className="flex items-start justify-between gap-4 px-4 py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                          {skill.title}
                        </span>
                        <span className="shrink-0 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-xs text-[var(--text-muted)]">
                          {skill.category}
                        </span>
                      </div>
                      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                        제출: {skill.author_username}
                        {skill.approved_by && ` · 승인: ${skill.approved_by}`}
                        {skill.approved_at &&
                          ` · ${new Date(skill.approved_at).toLocaleDateString("ko-KR")}`}
                        {` · 사용 ${skill.usage_count}회`}
                      </p>
                      {skill.description && (
                        <p className="mt-1 truncate text-xs text-[var(--text-secondary)]">
                          {skill.description}
                        </p>
                      )}
                    </div>

                    {revokeConfirm === skill.id ? (
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="text-xs text-[var(--text-muted)]">정말 삭제할까요?</span>
                        <button
                          onClick={() => handleRevoke(skill.id)}
                          className="rounded px-2 py-1 text-xs font-medium bg-[var(--danger)] text-white hover:opacity-80"
                        >
                          삭제
                        </button>
                        <button
                          onClick={() => setRevokeConfirm(null)}
                          className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
                        >
                          취소
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setRevokeConfirm(skill.id)}
                        className="shrink-0 rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--text-secondary)] hover:border-[var(--danger)] hover:text-[var(--danger)]"
                      >
                        삭제
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Rejected ── */}
        {activeTab === "rejected" && (
          <>
            <div className="border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">
                반려된 스킬
                {!loading && rejectedSkills.length > 0 && (
                  <span className="ml-2 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-xs font-medium text-[var(--text-muted)]">
                    {rejectedSkills.length}건
                  </span>
                )}
              </h2>
            </div>
            {loading ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">로딩 중...</div>
            ) : rejectedSkills.length === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">
                반려된 스킬이 없습니다.
              </div>
            ) : (
              <div className="divide-y divide-[var(--border)]">
                {rejectedSkills.map((skill) => (
                  <div key={skill.skill_id} className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-primary)]">
                        {skill.title ?? "(제목 없음)"}
                      </span>
                      {skill.category && (
                        <span className="shrink-0 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-xs text-[var(--text-muted)]">
                          {skill.category}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                      제출: {skill.author_username ?? "-"}
                      {skill.rejected_by && ` · 반려: ${skill.rejected_by}`}
                      {skill.rejected_at &&
                        ` · ${new Date(skill.rejected_at).toLocaleDateString("ko-KR")}`}
                    </p>
                    {skill.rejection_reason && (
                      <p className="mt-1.5 rounded bg-[var(--danger-light)] px-2.5 py-1.5 text-xs text-[var(--danger)]">
                        반려 사유: {skill.rejection_reason}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Audit Log ── */}
        {activeTab === "audit" && (
          <>
            <div className="border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">감사 로그</h2>
            </div>
            {loading ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">로딩 중...</div>
            ) : events.length === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">
                감사 이벤트가 없습니다.
              </div>
            ) : (
              <div className="divide-y divide-[var(--border)]">
                {events.map((event) => (
                  <div key={event.id} className="flex items-start gap-3 px-4 py-3">
                    <span
                      className={`mt-0.5 shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${
                        EVENT_TYPE_COLORS[event.event_type] ?? "bg-gray-50 text-gray-600 border-gray-200"
                      }`}
                    >
                      {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-[var(--text-primary)]">
                        <span className="font-medium">{event.actor_username}</span>
                        {event.skill_title && (
                          <span className="text-[var(--text-muted)]">
                            {" "}— {event.skill_title}
                          </span>
                        )}
                      </p>
                      {event.detail && (
                        <p className="mt-0.5 truncate text-xs text-[var(--text-muted)]">
                          {event.detail}
                        </p>
                      )}
                    </div>
                    <time className="shrink-0 text-xs text-[var(--text-muted)]">
                      {new Date(event.created_at).toLocaleString("ko-KR", {
                        dateStyle: "short",
                        timeStyle: "short",
                      })}
                    </time>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
