"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Announcement,
  AnnouncementCreatePayload,
  createAnnouncement,
  deleteAnnouncement,
  listAnnouncements,
  updateAnnouncement,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

const emptyDraft: AnnouncementCreatePayload = {
  title: "",
  content: "",
  is_pinned: false,
  expires_at: null,
};

export default function AnnouncementsPage() {
  const router = useRouter();
  const [items, setItems] = useState<Announcement[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState<AnnouncementCreatePayload>(emptyDraft);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    void refresh();
  }, [router]);

  const refresh = async () => {
    setLoading(true);
    setErr("");
    try {
      const data = await listAnnouncements();
      setItems(data.announcements);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "로드 실패");
    } finally {
      setLoading(false);
    }
  };

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 4000);
  };

  const handleCreate = async () => {
    if (!draft.title.trim() || !draft.content.trim()) {
      setErr("제목과 내용을 입력해주세요.");
      return;
    }
    setSaving(true);
    setErr("");
    try {
      // datetime-local input 은 tz 없는 ISO 문자열을 주므로 서버에서 UTC로 해석됨
      await createAnnouncement(draft);
      setDraft(emptyDraft);
      showToast("공지를 등록했습니다.");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "등록 실패");
    } finally {
      setSaving(false);
    }
  };

  const handleTogglePinned = async (a: Announcement) => {
    try {
      await updateAnnouncement(a.id, { is_pinned: !a.is_pinned });
      showToast(a.is_pinned ? "고정 해제됨" : "상단 고정됨");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "변경 실패");
    }
  };

  const handleToggleActive = async (a: Announcement) => {
    try {
      await updateAnnouncement(a.id, { is_active: !a.is_active });
      showToast(a.is_active ? "비활성화됨" : "활성화됨");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "변경 실패");
    }
  };

  const handleClearExpiry = async (a: Announcement) => {
    try {
      await updateAnnouncement(a.id, { expires_at: null });
      showToast("만료 해제됨");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "변경 실패");
    }
  };

  const handleDelete = async (a: Announcement) => {
    if (!confirm(`공지 "${a.title}"를 삭제하시겠습니까?`)) return;
    try {
      await deleteAnnouncement(a.id);
      showToast("삭제됨");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "삭제 실패");
    }
  };

  const pinnedCount = items.filter((a) => a.is_pinned && a.is_active).length;
  const expiredCount = items.filter(
    (a) => a.expires_at && new Date(a.expires_at) < new Date()
  ).length;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">공지 관리</h1>
          <p className="text-sm text-gray-500 mt-1">
            로그인 배너(상단 고정) · 허브 공지 관리 · GitHub #14
          </p>
        </div>
        <button
          onClick={refresh}
          className="px-3 py-1.5 text-sm border rounded hover:bg-gray-50"
        >
          새로고침
        </button>
      </header>

      {toast && (
        <div className="mb-4 px-4 py-2 bg-green-50 border border-green-300 text-green-800 rounded text-sm">
          {toast}
        </div>
      )}
      {err && (
        <div className="mb-4 px-4 py-2 bg-red-50 border border-red-300 text-red-800 rounded text-sm">
          {err}
        </div>
      )}

      <div className="mb-4 flex gap-3 text-sm text-gray-600">
        <span className="px-2 py-1 bg-blue-50 border border-blue-200 rounded">
          고정(활성): <strong>{pinnedCount}</strong>
        </span>
        <span className="px-2 py-1 bg-gray-50 border border-gray-200 rounded">
          만료 경과: <strong>{expiredCount}</strong>
        </span>
        <span className="px-2 py-1 bg-gray-50 border border-gray-200 rounded">
          전체: <strong>{items.length}</strong>
        </span>
      </div>

      <section className="mb-8 p-4 border rounded bg-gray-50">
        <h2 className="font-medium mb-3">새 공지 등록</h2>
        <div className="space-y-2">
          <input
            type="text"
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            placeholder="제목"
            className="w-full px-3 py-2 border rounded bg-white"
          />
          <textarea
            value={draft.content}
            onChange={(e) => setDraft({ ...draft, content: e.target.value })}
            placeholder="내용 (줄바꿈 포함)"
            rows={4}
            className="w-full px-3 py-2 border rounded bg-white"
          />
          <div className="flex items-center gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={draft.is_pinned}
                onChange={(e) => setDraft({ ...draft, is_pinned: e.target.checked })}
              />
              로그인 배너로 상단 고정 (is_pinned)
            </label>
            <label className="flex items-center gap-2">
              만료 시각:
              <input
                type="datetime-local"
                value={draft.expires_at ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, expires_at: e.target.value || null })
                }
                className="px-2 py-1 border rounded bg-white"
              />
              {draft.expires_at && (
                <button
                  type="button"
                  onClick={() => setDraft({ ...draft, expires_at: null })}
                  className="text-xs text-gray-500 underline"
                >
                  지우기
                </button>
              )}
            </label>
          </div>
          <div className="flex gap-2 pt-2">
            <button
              onClick={handleCreate}
              disabled={saving}
              className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? "등록 중..." : "등록"}
            </button>
            <button
              onClick={() => setDraft(emptyDraft)}
              className="px-4 py-2 border rounded hover:bg-gray-100"
              disabled={saving}
            >
              초기화
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            상단 고정 공지는 만료 시각을 지정하지 않으면 무기한 노출됩니다. 배포/점검
            공지는 반드시 만료 시각을 함께 설정하세요.
          </p>
        </div>
      </section>

      <section>
        <h2 className="font-medium mb-3">전체 공지</h2>
        {loading ? (
          <p className="text-sm text-gray-500">불러오는 중...</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-gray-500">등록된 공지가 없습니다.</p>
        ) : (
          <ul className="space-y-3">
            {items.map((a) => {
              const expired = a.expires_at ? new Date(a.expires_at) < new Date() : false;
              return (
                <li
                  key={a.id}
                  className={`p-4 border rounded ${
                    a.is_pinned && a.is_active ? "border-blue-400 bg-blue-50" : "bg-white"
                  } ${!a.is_active ? "opacity-60" : ""}`}
                >
                  <div className="flex justify-between items-start gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        {a.is_pinned && (
                          <span className="text-xs px-1.5 py-0.5 bg-blue-600 text-white rounded">
                            📌 고정
                          </span>
                        )}
                        {!a.is_active && (
                          <span className="text-xs px-1.5 py-0.5 bg-gray-400 text-white rounded">
                            비활성
                          </span>
                        )}
                        {expired && (
                          <span className="text-xs px-1.5 py-0.5 bg-orange-500 text-white rounded">
                            만료됨
                          </span>
                        )}
                        <strong className="truncate">{a.title}</strong>
                      </div>
                      <p className="text-sm text-gray-700 whitespace-pre-wrap">
                        {a.content}
                      </p>
                      <p className="text-xs text-gray-500 mt-2">
                        작성: {a.author_username ?? "—"} · 생성{" "}
                        {a.created_at ? new Date(a.created_at).toLocaleString("ko-KR") : "—"}
                        {a.expires_at && (
                          <>
                            {" "}· 만료 {new Date(a.expires_at).toLocaleString("ko-KR")}
                          </>
                        )}
                      </p>
                    </div>
                    <div className="flex flex-col gap-1 shrink-0">
                      <button
                        onClick={() => handleTogglePinned(a)}
                        className="text-xs px-2 py-1 border rounded hover:bg-gray-50"
                      >
                        {a.is_pinned ? "고정 해제" : "상단 고정"}
                      </button>
                      <button
                        onClick={() => handleToggleActive(a)}
                        className="text-xs px-2 py-1 border rounded hover:bg-gray-50"
                      >
                        {a.is_active ? "비활성화" : "활성화"}
                      </button>
                      {a.expires_at && (
                        <button
                          onClick={() => handleClearExpiry(a)}
                          className="text-xs px-2 py-1 border rounded hover:bg-gray-50"
                        >
                          만료 해제
                        </button>
                      )}
                      <button
                        onClick={() => handleDelete(a)}
                        className="text-xs px-2 py-1 border rounded text-red-600 hover:bg-red-50"
                      >
                        삭제
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
