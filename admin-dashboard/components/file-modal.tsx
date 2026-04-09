"use client";

import { useEffect, useRef } from "react";

interface FileModalProps {
  podName: string;
  username: string;
  onClose: () => void;
}

/**
 * 파일 업로드/다운로드 모달.
 * Pod의 파일 서버(/files/{pod_name}/)를 iframe으로 표시.
 */
export default function FileModal({ podName, username, onClose }: FileModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);

  const filesUrl = `https://claude.skons.net/files/${podName}/`;

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      ref={backdropRef}
      onClick={(e) => e.target === backdropRef.current && onClose()}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
    >
      <div className="relative flex h-[80vh] w-[90vw] max-w-4xl flex-col overflow-hidden rounded-xl bg-[var(--surface)] shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3">
          <div className="flex items-center gap-3">
            <span className="text-lg">&#128228;</span>
            <div>
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                파일 관리 — {username}
              </h2>
              <p className="text-xs text-[var(--text-muted)] font-mono">{podName}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={filesUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg)] transition-colors"
            >
              새 탭에서 열기 &#8599;
            </a>
            <button
              onClick={onClose}
              className="rounded-md p-1.5 text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-secondary)] transition-colors"
              aria-label="닫기"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* iframe */}
        <iframe
          src={filesUrl}
          className="flex-1 border-0"
          title={`Files for ${podName}`}
          sandbox="allow-same-origin allow-scripts allow-forms"
        />
      </div>
    </div>
  );
}
