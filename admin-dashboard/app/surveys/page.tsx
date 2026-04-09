"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getSurveys,
  getSurveyResponses,
  createSurvey,
  assignSurvey,
  getPhotoUrl,
  searchMembers,
  Survey,
  SurveyResponse,
  SurveyQuestion,
  OGuardProfile,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

const REFRESH_INTERVAL = 30_000;

function statusBadge(status: string) {
  const base =
    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (status) {
    case "active":
      return (
        <span className={`${base} bg-[var(--success-light)] text-[var(--success)]`}>{status}</span>
      );
    case "draft":
      return (
        <span className={`${base} bg-[var(--warning-light)] text-[var(--warning)]`}>
          {status}
        </span>
      );
    case "closed":
      return (
        <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>{status}</span>
      );
    default:
      return (
        <span className={`${base} bg-[var(--primary-light)] text-[var(--primary)]`}>{status}</span>
      );
  }
}

function formatDate(iso: string | null) {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

// ---------- Photo Thumbnail ----------

function PhotoThumbnail({ s3Key }: { s3Key: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [enlarged, setEnlarged] = useState(false);

  const loadUrl = async () => {
    if (url || loading) return;
    setLoading(true);
    try {
      const res = await getPhotoUrl(s3Key);
      setUrl(res.url);
    } catch {
      setUrl(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUrl();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s3Key]);

  if (loading)
    return (
      <span className="inline-block h-10 w-10 rounded bg-[var(--surface-hover)] animate-pulse" />
    );
  if (!url) return <span className="text-xs text-[var(--text-muted)]">(no photo)</span>;

  return (
    <>
      <img
        src={url}
        alt="photo"
        className="h-10 w-10 rounded object-cover cursor-pointer border border-[var(--border)]"
        onClick={() => setEnlarged(true)}
      />
      {enlarged && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setEnlarged(false)}
        >
          <img
            src={url}
            alt="photo enlarged"
            className="max-h-[80vh] max-w-[80vw] rounded-lg shadow-xl"
          />
        </div>
      )}
    </>
  );
}

// ---------- Assign Modal ----------

function AssignModal({
  survey,
  onClose,
}: {
  survey: Survey;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<OGuardProfile[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  const doSearch = async () => {
    if (query.length < 2) return;
    setSearching(true);
    try {
      const res = await searchMembers(query);
      setResults(res.results);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  const toggleUser = (username: string) => {
    setSelected((prev) =>
      prev.includes(username)
        ? prev.filter((u) => u !== username)
        : [...prev, username]
    );
  };

  const handleAssign = async () => {
    if (selected.length === 0) return;
    setSubmitting(true);
    try {
      const res = await assignSurvey(survey.id, selected);
      setMessage(`${Array.isArray(res) ? res.length : selected.length}명에게 배정 완료`);
      setTimeout(onClose, 1200);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "배정 실패");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-lg bg-[var(--surface)] p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold text-[var(--text-primary)]">
            설문 배정 &mdash; {survey.title}
          </h3>
          <button
            onClick={onClose}
            className="text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
          >
            &times;
          </button>
        </div>

        {/* Search */}
        <div className="mb-3 flex gap-2">
          <input
            type="text"
            placeholder="이름 또는 사번 검색..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
            className="flex-1 rounded-md border border-[var(--border-strong)] px-3 py-2 text-sm focus:border-[var(--primary)] focus:outline-none"
          />
          <button
            onClick={doSearch}
            disabled={searching || query.length < 2}
            className="rounded-md bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--primary-hover)] disabled:opacity-50"
          >
            검색
          </button>
        </div>

        {/* Results */}
        {results.length > 0 && (
          <div className="mb-3 max-h-48 overflow-y-auto rounded border border-[var(--border)]">
            {results.map((p) => (
              <label
                key={p.username}
                className="flex cursor-pointer items-center gap-2 px-3 py-2 hover:bg-[var(--bg)]"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(p.username)}
                  onChange={() => toggleUser(p.username)}
                  className="rounded border-[var(--border-strong)]"
                />
                <span className="text-sm text-[var(--text-primary)]">
                  {p.first_name || p.username}
                </span>
                <span className="text-xs text-[var(--text-muted)]">({p.username})</span>
                {p.team_name && (
                  <span className="text-xs text-[var(--text-muted)]">
                    {p.team_name}
                  </span>
                )}
              </label>
            ))}
          </div>
        )}

        {/* Selected count */}
        {selected.length > 0 && (
          <div className="mb-3 text-sm text-[var(--text-secondary)]">
            {selected.length}명 선택됨
          </div>
        )}

        {message && (
          <div className="mb-3 text-sm text-[var(--primary)]">{message}</div>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md border border-[var(--border-strong)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg)]"
          >
            닫기
          </button>
          <button
            onClick={handleAssign}
            disabled={submitting || selected.length === 0}
            className="rounded-md bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--primary-hover)] disabled:opacity-50"
          >
            {submitting ? "배정 중..." : "배정"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Question Builder ----------

interface QuestionDraft {
  type: "text" | "photo" | "choice";
  label: string;
  required: boolean;
  options: string[];
}

function emptyQuestion(): QuestionDraft {
  return { type: "text", label: "", required: false, options: [] };
}

// ---------- Create Survey Form ----------

function CreateSurveyForm({ onCreated }: { onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [questions, setQuestions] = useState<QuestionDraft[]>([emptyQuestion()]);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  const updateQuestion = (idx: number, patch: Partial<QuestionDraft>) => {
    setQuestions((prev) =>
      prev.map((q, i) => (i === idx ? { ...q, ...patch } : q))
    );
  };

  const removeQuestion = (idx: number) => {
    setQuestions((prev) => prev.filter((_, i) => i !== idx));
  };

  const addOption = (qIdx: number) => {
    setQuestions((prev) =>
      prev.map((q, i) =>
        i === qIdx ? { ...q, options: [...q.options, ""] } : q
      )
    );
  };

  const updateOption = (qIdx: number, oIdx: number, value: string) => {
    setQuestions((prev) =>
      prev.map((q, i) =>
        i === qIdx
          ? {
              ...q,
              options: q.options.map((o, j) => (j === oIdx ? value : o)),
            }
          : q
      )
    );
  };

  const removeOption = (qIdx: number, oIdx: number) => {
    setQuestions((prev) =>
      prev.map((q, i) =>
        i === qIdx
          ? { ...q, options: q.options.filter((_, j) => j !== oIdx) }
          : q
      )
    );
  };

  const handleSubmit = async () => {
    if (!title.trim()) return;
    const validQuestions = questions.filter((q) => q.label.trim());
    if (validQuestions.length === 0) return;

    setSubmitting(true);
    setMessage("");
    try {
      const payload: SurveyQuestion[] = validQuestions.map((q) => ({
        type: q.type,
        label: q.label,
        required: q.required,
        ...(q.type === "choice" && q.options.length > 0
          ? { options: q.options.filter((o) => o.trim()) }
          : {}),
      }));

      await createSurvey({ title, description, questions: payload });
      setMessage("설문 생성 완료");
      setTitle("");
      setDescription("");
      setQuestions([emptyQuestion()]);
      onCreated();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "생성 실패");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-base font-semibold text-[var(--text-primary)]">
          새 설문 양식 만들기
        </h2>
      </div>
      <div className="p-4 space-y-4">
        {/* Title */}
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
            제목
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="설문 제목"
            className="w-full rounded-md border border-[var(--border-strong)] px-3 py-2 text-sm focus:border-[var(--primary)] focus:outline-none"
          />
        </div>

        {/* Description */}
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
            설명
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="설문 설명 (선택)"
            rows={2}
            className="w-full rounded-md border border-[var(--border-strong)] px-3 py-2 text-sm focus:border-[var(--primary)] focus:outline-none resize-none"
          />
        </div>

        {/* Questions */}
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
            질문 목록
          </label>
          <div className="space-y-3">
            {questions.map((q, qIdx) => (
              <div
                key={qIdx}
                className="rounded-md border border-[var(--border)] bg-[var(--bg)] p-3"
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-medium text-[var(--text-muted)]">
                    Q{qIdx + 1}
                  </span>
                  <select
                    value={q.type}
                    onChange={(e) =>
                      updateQuestion(qIdx, {
                        type: e.target.value as QuestionDraft["type"],
                        options:
                          e.target.value === "choice" ? q.options : [],
                      })
                    }
                    className="rounded border border-[var(--border-strong)] px-2 py-1 text-xs focus:border-[var(--primary)] focus:outline-none"
                  >
                    <option value="text">텍스트</option>
                    <option value="photo">사진</option>
                    <option value="choice">객관식</option>
                  </select>
                  <input
                    type="text"
                    value={q.label}
                    onChange={(e) =>
                      updateQuestion(qIdx, { label: e.target.value })
                    }
                    placeholder="질문 내용"
                    className="flex-1 rounded border border-[var(--border-strong)] px-2 py-1 text-sm focus:border-[var(--primary)] focus:outline-none"
                  />
                  <label className="flex items-center gap-1 text-xs text-[var(--text-muted)] whitespace-nowrap">
                    <input
                      type="checkbox"
                      checked={q.required}
                      onChange={(e) =>
                        updateQuestion(qIdx, { required: e.target.checked })
                      }
                      className="rounded border-[var(--border-strong)]"
                    />
                    필수
                  </label>
                  {questions.length > 1 && (
                    <button
                      onClick={() => removeQuestion(qIdx)}
                      className="text-[var(--danger)] hover:text-[var(--danger)] text-sm"
                      title="질문 삭제"
                    >
                      &times;
                    </button>
                  )}
                </div>

                {/* Choice options */}
                {q.type === "choice" && (
                  <div className="ml-6 space-y-1">
                    {q.options.map((opt, oIdx) => (
                      <div key={oIdx} className="flex items-center gap-2">
                        <span className="text-xs text-[var(--text-muted)]">
                          {oIdx + 1}.
                        </span>
                        <input
                          type="text"
                          value={opt}
                          onChange={(e) =>
                            updateOption(qIdx, oIdx, e.target.value)
                          }
                          placeholder={`선택지 ${oIdx + 1}`}
                          className="flex-1 rounded border border-[var(--border-strong)] px-2 py-1 text-xs focus:border-[var(--primary)] focus:outline-none"
                        />
                        <button
                          onClick={() => removeOption(qIdx, oIdx)}
                          className="text-[var(--danger)] hover:text-[var(--danger)] text-xs"
                        >
                          &times;
                        </button>
                      </div>
                    ))}
                    <button
                      onClick={() => addOption(qIdx)}
                      className="text-xs text-[var(--primary)] hover:text-[var(--primary)]"
                    >
                      + 선택지 추가
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
          <button
            onClick={() => setQuestions([...questions, emptyQuestion()])}
            className="mt-2 text-sm text-[var(--primary)] hover:text-[var(--primary)]"
          >
            + 질문 추가
          </button>
        </div>

        {message && (
          <div
            className={`text-sm ${message.includes("완료") ? "text-[var(--success)]" : "text-[var(--danger)]"}`}
          >
            {message}
          </div>
        )}

        <div className="flex justify-end">
          <button
            onClick={handleSubmit}
            disabled={submitting || !title.trim()}
            className="rounded-md bg-[var(--primary)] px-6 py-2 text-sm font-medium text-white hover:bg-[var(--primary-hover)] disabled:opacity-50"
          >
            {submitting ? "생성 중..." : "설문 생성"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Response Panel ----------

function ResponsePanel({
  survey,
  responses,
  loading,
}: {
  survey: Survey;
  responses: SurveyResponse[];
  loading: boolean;
}) {
  const choiceBadge =
    "inline-flex items-center rounded-full bg-[var(--info-light)] text-[var(--info)] px-2 py-0.5 text-xs font-medium";

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm h-full">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-base font-semibold text-[var(--text-primary)] truncate">
          {survey.title} &mdash; 응답
        </h2>
        <p className="text-xs text-[var(--text-muted)] mt-0.5">
          총 {responses.length}건
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
          응답을 불러오는 중...
        </div>
      ) : responses.length === 0 ? (
        <div className="py-12 text-center text-sm text-[var(--text-muted)]">
          아직 응답이 없습니다.
        </div>
      ) : (
        <div className="overflow-x-auto overflow-y-auto max-h-[60vh]">
          <table className="min-w-full divide-y divide-[var(--border)]">
            <thead className="bg-[var(--bg)] sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  응답자
                </th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  완료시간
                </th>
                {survey.questions.map((q, i) => (
                  <th
                    key={i}
                    className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]"
                  >
                    {q.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
              {responses.map((r, rIdx) => (
                <tr key={rIdx} className="hover:bg-[var(--bg)]">
                  <td className="whitespace-nowrap px-3 py-2 text-sm text-[var(--text-primary)]">
                    {r.responder_username}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-sm text-[var(--text-secondary)]">
                    {formatDate(r.completed_at)}
                  </td>
                  {survey.questions.map((q, qIdx) => {
                    const answerEntry = Array.isArray(r.answers)
                      ? (r.answers as Array<{ question_idx: number; type: string; value: string | null; s3_key: string | null }>).find((a) => a.question_idx === qIdx)
                      : null;
                    const answer = answerEntry
                      ? (answerEntry.type === "photo" ? answerEntry.s3_key : answerEntry.value)
                      : null;
                    return (
                      <td key={qIdx} className="px-3 py-2 text-sm">
                        {q.type === "photo" && answer ? (
                          <PhotoThumbnail s3Key={String(answer)} />
                        ) : q.type === "choice" && answer ? (
                          <span className={choiceBadge}>{String(answer)}</span>
                        ) : answer ? (
                          <span className="text-[var(--text-secondary)]">
                            {String(answer)}
                          </span>
                        ) : (
                          <span className="text-[var(--text-muted)]">-</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------- Main Page ----------

export default function SurveysPage() {
  const router = useRouter();
  const [surveys, setSurveys] = useState<Survey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Selected survey & responses
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [responses, setResponses] = useState<SurveyResponse[]>([]);
  const [responsesLoading, setResponsesLoading] = useState(false);

  // Assign modal
  const [assignTarget, setAssignTarget] = useState<Survey | null>(null);

  const fetchSurveys = useCallback(async () => {
    try {
      const res = await getSurveys();
      setSurveys(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load surveys");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    fetchSurveys();
    const timer = setInterval(fetchSurveys, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchSurveys]);

  // Fetch responses when selecting a survey
  useEffect(() => {
    if (selectedId === null) {
      setResponses([]);
      return;
    }
    let cancelled = false;
    setResponsesLoading(true);
    getSurveyResponses(selectedId)
      .then((res) => {
        if (!cancelled) setResponses(res);
      })
      .catch(() => {
        if (!cancelled) setResponses([]);
      })
      .finally(() => {
        if (!cancelled) setResponsesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const selectedSurvey = surveys.find((s) => s.id === selectedId) ?? null;
  const activeCount = surveys.filter((s) => s.status === "active").length;
  const totalResponses = surveys.reduce(
    (sum, s) => sum + (s.response_count ?? 0),
    0
  );

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">
            {error}
          </div>
        )}

        {/* Stats */}
        <div className="mb-6 grid grid-cols-3 gap-4">
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">전체 양식</div>
            <div className="mt-1 text-2xl font-bold text-[var(--text-primary)]">
              {surveys.length}
            </div>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">활성</div>
            <div className="mt-1 text-2xl font-bold text-[var(--success)]">
              {activeCount}
            </div>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="text-sm text-[var(--text-muted)]">총 응답 수</div>
            <div className="mt-1 text-2xl font-bold text-[var(--info)]">
              {totalResponses}
            </div>
          </div>
        </div>

        {/* Two-column: Survey list + Responses */}
        <div className="mb-6 grid grid-cols-5 gap-4">
          {/* Left: Survey List (60%) */}
          <div className="col-span-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <div className="border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">
                설문 목록
              </h2>
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
                데이터를 불러오는 중...
              </div>
            ) : surveys.length === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--text-muted)]">
                설문이 없습니다.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-[var(--border)]">
                  <thead className="bg-[var(--bg)]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                        제목
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                        생성자
                      </th>
                      <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                        질문 수
                      </th>
                      <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                        응답 수
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                        상태
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                        생성일
                      </th>
                      <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                        배정
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                    {surveys.map((s) => (
                      <tr
                        key={s.id}
                        className={`cursor-pointer transition-colors ${
                          selectedId === s.id
                            ? "bg-[var(--primary-light)]"
                            : "hover:bg-[var(--bg)]"
                        }`}
                        onClick={() =>
                          setSelectedId(selectedId === s.id ? null : s.id)
                        }
                      >
                        <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                          {s.title}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                          {s.owner_username}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-[var(--text-secondary)]">
                          {s.questions?.length ?? 0}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-sm text-center text-[var(--text-secondary)]">
                          {s.response_count ?? 0}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-sm">
                          {statusBadge(s.status)}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                          {formatDate(s.created_at)}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setAssignTarget(s);
                            }}
                            className="rounded bg-[var(--primary-light)] px-2 py-1 text-xs font-medium text-[var(--primary)] hover:bg-[var(--primary-light)]"
                          >
                            배정
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Right: Responses (40%) */}
          <div className="col-span-2">
            {selectedSurvey ? (
              <ResponsePanel
                survey={selectedSurvey}
                responses={responses}
                loading={responsesLoading}
              />
            ) : (
              <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-[var(--border-strong)] bg-[var(--surface)] p-8">
                <p className="text-sm text-[var(--text-muted)]">
                  설문을 선택하면 응답을 확인할 수 있습니다.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Create Survey Form */}
        <CreateSurveyForm onCreated={fetchSurveys} />
      </main>

      {/* Assign Modal */}
      {assignTarget && (
        <AssignModal
          survey={assignTarget}
          onClose={() => setAssignTarget(null)}
        />
      )}
    </>
  );
}
