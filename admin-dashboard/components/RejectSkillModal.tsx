"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  skillTitle: string;
  onConfirm: (reason: string) => Promise<void>;
  onClose: () => void;
}

export default function RejectSkillModal({ skillTitle, onConfirm, onClose }: Props) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  async function handleSubmit() {
    const trimmed = reason.trim();
    if (!trimmed) {
      setError("반려 사유를 입력해주세요.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await onConfirm(trimmed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "반려 처리에 실패했습니다.");
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-md rounded-xl border border-[var(--border)] bg-[var(--surface)] shadow-xl">
        {/* Header */}
        <div className="border-b border-[var(--border)] px-6 py-4">
          <h2 className="text-base font-semibold text-[var(--text-primary)]">스킬 반려</h2>
          <p className="mt-0.5 truncate text-sm text-[var(--text-muted)]">{skillTitle}</p>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-3">
          <label className="block text-sm font-medium text-[var(--text-secondary)]">
            반려 사유 <span className="text-[var(--danger)]">*</span>
          </label>
          <textarea
            ref={textareaRef}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="반려 사유를 입력하세요."
            rows={4}
            className="w-full resize-none rounded-lg border border-[var(--border-strong)] bg-[var(--bg)] px-3 py-2.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
          />
          {error && (
            <p className="text-xs text-[var(--danger)]">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-6 py-4">
          <button
            onClick={onClose}
            disabled={submitting}
            className="rounded-md border border-[var(--border-strong)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] disabled:opacity-40"
          >
            취소
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="rounded-md bg-[var(--danger)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            {submitting ? "처리 중..." : "반려 확인"}
          </button>
        </div>
      </div>
    </div>
  );
}
