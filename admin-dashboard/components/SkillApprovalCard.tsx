"use client";

import { useEffect, useState } from "react";
import {
  approveSkill,
  fetchSkillApprovalProgress,
  rejectSkill,
  type SkillApprovalProgress,
  type SkillPendingProgressItem,
} from "@/lib/api";
import RejectSkillModal from "./RejectSkillModal";

interface Props {
  item: SkillPendingProgressItem;
  onRefresh: () => void;
  onToast: (type: "success" | "error", message: string) => void;
}

function CategoryBadge({ category }: { category: string }) {
  return (
    <span className="inline-flex items-center rounded-full bg-[var(--primary-light)] px-2 py-0.5 text-xs font-medium text-[var(--primary)]">
      {category}
    </span>
  );
}

function ProgressBar({ current, required }: { current: number; required: number }) {
  const pct = required > 0 ? Math.min((current / required) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--surface-hover)]">
        <div
          className="h-full rounded-full bg-[var(--primary)] transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-[var(--text-muted)]">
        {current} / {required}
      </span>
    </div>
  );
}

export default function SkillApprovalCard({ item, onRefresh, onToast }: Props) {
  const [detail, setDetail] = useState<SkillApprovalProgress | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [processing, setProcessing] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchSkillApprovalProgress(item.skill_id)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch(() => { /* detail 로드 실패 시 리스트 데이터로 폴백 */ })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [item.skill_id]);

  const sodBlocked = detail ? detail.sod_blocked : false;
  const canApprove = detail ? detail.can_current_admin_approve : false;

  const approveDisabled = detailLoading || processing || sodBlocked || !canApprove;
  const rejectDisabled = detailLoading || processing || sodBlocked;

  let approveTooltip = "";
  if (sodBlocked) approveTooltip = "자기 제출은 승인 불가";
  else if (!canApprove && !detailLoading) approveTooltip = "이미 승인한 스킬입니다";

  let rejectTooltip = "";
  if (sodBlocked) rejectTooltip = "자기 제출은 반려 불가";

  async function handleApprove() {
    setProcessing(true);
    try {
      await approveSkill(item.skill_id);
      onToast("success", `"${item.title}" 승인 완료`);
      onRefresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "승인 처리 실패";
      if (msg === "duplicate_approval") {
        onToast("error", "이미 승인한 스킬입니다.");
      } else if (msg === "sod_violation") {
        onToast("error", "자기 제출 스킬은 승인할 수 없습니다.");
      } else {
        onToast("error", `승인 실패: ${msg}`);
      }
    } finally {
      setProcessing(false);
    }
  }

  async function handleRejectConfirm(reason: string) {
    await rejectSkill(item.skill_id, reason);
    setShowRejectModal(false);
    onToast("success", `"${item.title}" 반려 완료`);
    onRefresh();
  }

  return (
    <>
      <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 shadow-sm transition-shadow hover:shadow-md">
        {/* Title + category */}
        <div className="mb-2 flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <CategoryBadge category={item.category} />
            <span className="text-sm font-medium text-[var(--text-primary)] leading-snug">
              {item.title}
            </span>
          </div>
        </div>

        {/* Meta */}
        <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
          <span>작성자 <span className="font-mono text-[var(--text-secondary)]">{item.author_username}</span></span>
          <span>오너 <span className="font-mono text-[var(--text-secondary)]">{item.owner_username}</span></span>
        </div>

        {/* Progress */}
        <div className="mb-4">
          <p className="mb-1 text-xs text-[var(--text-muted)]">승인 진행률</p>
          <ProgressBar
            current={detail ? detail.current_approvers.length : item.current_approvals}
            required={detail ? detail.required_approvals : item.required_approvals}
          />
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-2">
          {detailLoading && (
            <span className="text-xs text-[var(--text-muted)]">로딩 중...</span>
          )}

          <div title={rejectTooltip || undefined}>
            <button
              onClick={() => setShowRejectModal(true)}
              disabled={rejectDisabled}
              className="rounded-md border border-[var(--danger)] px-3 py-1.5 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              반려
            </button>
          </div>

          <div title={approveTooltip || undefined}>
            <button
              onClick={handleApprove}
              disabled={approveDisabled}
              className="rounded-md bg-[var(--primary)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--primary-hover)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {processing ? "처리 중..." : "승인"}
            </button>
          </div>
        </div>
      </div>

      {showRejectModal && (
        <RejectSkillModal
          skillTitle={item.title}
          onConfirm={handleRejectConfirm}
          onClose={() => setShowRejectModal(false)}
        />
      )}
    </>
  );
}
