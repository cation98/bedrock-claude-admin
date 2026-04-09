"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getQuotaTemplates,
  getQuotaAssignments,
  createQuotaTemplate,
  updateQuotaTemplate,
  deleteQuotaTemplate,
  assignQuota,
  checkQuota,
  getUsers,
  type QuotaTemplate,
  type QuotaAssignment,
  type QuotaCheckResult,
  type User,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import Pagination from "@/components/pagination";

const CYCLE_OPTIONS = [
  { value: "daily", label: "일별" },
  { value: "weekly", label: "주별" },
  { value: "monthly", label: "월별" },
];

const CYCLE_LABEL: Record<string, string> = {
  daily: "일별",
  weekly: "주별",
  monthly: "월별",
};

export default function QuotaPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<QuotaTemplate[]>([]);
  const [assignments, setAssignments] = useState<QuotaAssignment[]>([]);
  const [approvedUsers, setApprovedUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Template editor
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editCostUsd, setEditCostUsd] = useState("10");
  const [editCycle, setEditCycle] = useState("monthly");
  const [editUnlimited, setEditUnlimited] = useState(false);

  // Assignment management
  const [checkedUsers, setCheckedUsers] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [assignPage, setAssignPage] = useState(1);
  const PAGE_SIZE = 10;

  // Quota check results
  const [quotaChecks, setQuotaChecks] = useState<Record<string, QuotaCheckResult>>({});

  const fetchData = useCallback(async () => {
    try {
      const [tRes, aRes, uRes] = await Promise.all([
        getQuotaTemplates(),
        getQuotaAssignments(),
        getUsers(),
      ]);
      setTemplates(tRes.templates);
      setAssignments(aRes.assignments);
      setApprovedUsers(uRes.users.filter((u) => u.is_approved));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 조회 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) { router.replace("/"); return; }
    fetchData();
  }, [router, fetchData]);

  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(""), 3000);
    return () => clearTimeout(t);
  }, [success]);

  // Load quota check for assignments
  useEffect(() => {
    if (assignments.length === 0) return;
    const usernames = assignments.map(a => a.username);
    Promise.allSettled(usernames.map(u => checkQuota(u))).then(results => {
      const checks: Record<string, QuotaCheckResult> = {};
      results.forEach((r, i) => {
        if (r.status === "fulfilled") checks[usernames[i]] = r.value;
      });
      setQuotaChecks(checks);
    });
  }, [assignments]);

  function clearMessages() { setError(""); setSuccess(""); }

  function selectTemplate(name: string) {
    const tmpl = templates.find(t => t.name === name);
    if (!tmpl) return;
    setSelectedTemplate(name);
    setIsCreatingNew(false);
    setEditName(tmpl.name);
    setEditDesc(tmpl.description);
    setEditCostUsd(String(tmpl.cost_limit_usd));
    setEditCycle(tmpl.refresh_cycle);
    setEditUnlimited(tmpl.is_unlimited);
  }

  function startNewTemplate() {
    setSelectedTemplate(null);
    setIsCreatingNew(true);
    setEditName("");
    setEditDesc("");
    setEditCostUsd("10");
    setEditCycle("monthly");
    setEditUnlimited(false);
  }

  async function handleSaveTemplate() {
    clearMessages();
    const data = {
      name: editName.trim(),
      description: editDesc.trim(),
      cost_limit_usd: parseFloat(editCostUsd) || 0,
      refresh_cycle: editCycle,
      is_unlimited: editUnlimited,
    };
    if (!data.name) { setError("정책 이름을 입력하세요"); return; }
    try {
      if (isCreatingNew) {
        await createQuotaTemplate(data);
        setSuccess(`"${data.name}" 정책이 생성되었습니다.`);
      } else {
        const tmpl = templates.find(t => t.name === selectedTemplate);
        if (tmpl) {
          await updateQuotaTemplate(tmpl.id, data);
          setSuccess(`"${data.name}" 정책이 수정되었습니다.`);
        }
      }
      setIsCreatingNew(false);
      setSelectedTemplate(null);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "정책 저장 실패");
    }
  }

  async function handleDeleteTemplate() {
    if (!selectedTemplate) return;
    const tmpl = templates.find(t => t.name === selectedTemplate);
    if (!tmpl || !confirm(`"${tmpl.name}" 정책을 삭제하시겠습니까?`)) return;
    clearMessages();
    try {
      await deleteQuotaTemplate(tmpl.id);
      setSuccess("정책이 삭제되었습니다.");
      setSelectedTemplate(null);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "삭제 실패");
    }
  }

  async function handleBulkAssign(templateName: string) {
    if (checkedUsers.size === 0) return;
    clearMessages();
    try {
      await assignQuota({ usernames: Array.from(checkedUsers), template_name: templateName });
      setSuccess(`${checkedUsers.size}명에게 "${templateName}" 정책 적용`);
      setCheckedUsers(new Set());
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "정책 적용 실패");
    }
  }

  // Build merged list: all approved users with their assignment info
  const userList = approvedUsers.map(u => {
    const assignment = assignments.find(a => a.username === u.username);
    const quota = quotaChecks[u.username];
    return { ...u, assignment, quota };
  });

  const filteredUsers = userList.filter(u => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (u.name ?? u.username).toLowerCase().includes(q) ||
      u.username.toLowerCase().includes(q) ||
      (u.assignment?.template_name ?? "").toLowerCase().includes(q);
  });

  useEffect(() => { setAssignPage(1); }, [searchQuery]);

  const totalPages = Math.max(1, Math.ceil(filteredUsers.length / PAGE_SIZE));
  const safePage = Math.min(assignPage, totalPages);
  const paginatedUsers = filteredUsers.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const krwRate = 1450; // approximate exchange rate

  if (loading) {
    return <div className="flex items-center justify-center py-20 text-[var(--text-muted)]">데이터를 불러오는 중...</div>;
  }

  return (
    <div className="px-6 py-8">
      {error && <div className="mb-4 rounded-md bg-[var(--error-light)] px-4 py-3 text-sm text-[var(--error)]">{error}</div>}
      {success && <div className="mb-4 rounded-md bg-[var(--success-light)] px-4 py-3 text-sm text-[var(--success)]">{success}</div>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* ═══ LEFT: User assignments (60%) ═══ */}
        <div className="lg:col-span-3">
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                사용자 토큰 정책 ({approvedUsers.length}명)
              </h2>
              <div className="flex items-center gap-2">
                {checkedUsers.size > 0 && (
                  <select
                    className="rounded-md border border-[var(--primary)] bg-[var(--primary-light)] px-2 py-1 text-xs text-[var(--primary)]"
                    value=""
                    onChange={(e) => { if (e.target.value) handleBulkAssign(e.target.value); }}
                  >
                    <option value="">{checkedUsers.size}명 선택 — 일괄 적용...</option>
                    {templates.map(t => (
                      <option key={t.name} value={t.name}>{t.name}</option>
                    ))}
                  </select>
                )}
                <input
                  type="text"
                  placeholder="검색..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="rounded-md border border-[var(--border-strong)] px-2.5 py-1 text-xs w-36"
                />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-[var(--bg)] text-xs text-[var(--text-muted)] uppercase">
                  <tr>
                    <th className="px-4 py-2 text-left w-8">
                      <input
                        type="checkbox"
                        checked={filteredUsers.length > 0 && checkedUsers.size === filteredUsers.length}
                        onChange={(e) => {
                          if (e.target.checked) setCheckedUsers(new Set(filteredUsers.map(u => u.username)));
                          else setCheckedUsers(new Set());
                        }}
                        className="h-3.5 w-3.5 rounded border-[var(--border-strong)] text-[var(--primary)]"
                      />
                    </th>
                    <th className="px-4 py-2 text-left">사용자</th>
                    <th className="px-4 py-2 text-left">정책</th>
                    <th className="px-4 py-2 text-right">한도</th>
                    <th className="px-4 py-2 text-right">사용</th>
                    <th className="px-4 py-2 text-right">잔여</th>
                    <th className="px-4 py-2 text-center">상태</th>
                    <th className="px-4 py-2 text-right">빠른 설정</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {paginatedUsers.length === 0 ? (
                    <tr><td colSpan={8} className="px-4 py-8 text-center text-xs text-[var(--text-muted)]">
                      {searchQuery ? "검색 결과 없음" : "사용자 없음"}
                    </td></tr>
                  ) : paginatedUsers.map(u => {
                    const q = u.quota;
                    const pct = q && !q.is_unlimited && q.cost_limit_usd > 0
                      ? Math.min(100, (q.current_usage_usd / q.cost_limit_usd) * 100)
                      : 0;
                    return (
                      <tr key={u.username} className="hover:bg-[var(--bg)]/50">
                        <td className="px-4 py-2">
                          <input
                            type="checkbox"
                            checked={checkedUsers.has(u.username)}
                            onChange={(e) => {
                              const next = new Set(checkedUsers);
                              if (e.target.checked) next.add(u.username);
                              else next.delete(u.username);
                              setCheckedUsers(next);
                            }}
                            className="h-3.5 w-3.5 rounded border-[var(--border-strong)] text-[var(--primary)]"
                          />
                        </td>
                        <td className="px-4 py-2">
                          <span className="font-medium text-[var(--text-primary)]">{u.name ?? u.username}</span>
                          <span className="ml-1 text-xs text-[var(--text-muted)]">({u.username})</span>
                        </td>
                        <td className="px-4 py-2">
                          {u.assignment ? (
                            <span className="inline-flex items-center rounded-full bg-[var(--info-light)] px-2.5 py-0.5 text-xs font-medium text-[var(--info)]">
                              {u.assignment.template_name}
                            </span>
                          ) : (
                            <span className="text-xs text-[var(--text-muted)]">미설정</span>
                          )}
                        </td>
                        <td className="px-4 py-2 text-right text-xs tabular-nums text-[var(--text-secondary)]">
                          {q ? (q.is_unlimited ? "무제한" : `$${q.cost_limit_usd.toFixed(2)}`) : "-"}
                        </td>
                        <td className="px-4 py-2 text-right text-xs tabular-nums text-[var(--text-secondary)]">
                          {q ? `$${q.current_usage_usd.toFixed(2)}` : "-"}
                        </td>
                        <td className="px-4 py-2 text-right text-xs tabular-nums">
                          {q ? (
                            q.is_unlimited ? (
                              <span className="text-[var(--success)]">∞</span>
                            ) : (
                              <span className={q.is_exceeded ? "text-[var(--danger)] font-medium" : "text-[var(--text-secondary)]"}>
                                ${q.remaining_usd.toFixed(2)}
                              </span>
                            )
                          ) : "-"}
                        </td>
                        <td className="px-4 py-2 text-center">
                          {q ? (
                            q.is_unlimited ? (
                              <span className="inline-flex rounded-full bg-[var(--success-light)] px-2 py-0.5 text-xs font-medium text-[var(--success)]">무제한</span>
                            ) : q.is_exceeded ? (
                              <span className="inline-flex rounded-full bg-[var(--danger-light)] px-2 py-0.5 text-xs font-medium text-[var(--danger)]">초과</span>
                            ) : pct > 80 ? (
                              <span className="inline-flex rounded-full bg-[var(--warning-light)] px-2 py-0.5 text-xs font-medium text-[var(--warning)]">{pct.toFixed(0)}%</span>
                            ) : (
                              <span className="inline-flex rounded-full bg-[var(--success-light)] px-2 py-0.5 text-xs font-medium text-[var(--success)]">{pct.toFixed(0)}%</span>
                            )
                          ) : (
                            <span className="text-xs text-[var(--text-muted)]">-</span>
                          )}
                        </td>
                        <td className="px-4 py-2 text-right">
                          <select
                            className="rounded border border-[var(--border)] px-1.5 py-0.5 text-xs"
                            value=""
                            onChange={async (e) => {
                              if (!e.target.value) return;
                              try {
                                await assignQuota({ usernames: [u.username], template_name: e.target.value });
                                setSuccess(`${u.name ?? u.username}에게 "${e.target.value}" 적용`);
                                fetchData();
                              } catch (err) { setError(err instanceof Error ? err.message : "적용 실패"); }
                            }}
                          >
                            <option value="">
                              {u.assignment ? `현재: ${u.assignment.template_name}` : "정책 선택..."}
                            </option>
                            {templates.map(t => (
                              <option key={t.name} value={t.name}>{t.name}</option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination
              currentPage={safePage}
              totalPages={totalPages}
              totalItems={filteredUsers.length}
              itemsPerPage={PAGE_SIZE}
              onPageChange={setAssignPage}
            />
          </div>
        </div>

        {/* ═══ RIGHT: Template management (40%) ═══ */}
        <div className="space-y-4 lg:col-span-2">
          {/* Template list */}
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <div className="border-b border-[var(--border)] px-4 py-3">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">정책 템플릿</h2>
            </div>
            <div className="divide-y divide-[var(--border)]">
              {templates.map(t => {
                const assignedCount = assignments.filter(a => a.template_name === t.name).length;
                return (
                  <button
                    key={t.name}
                    onClick={() => selectTemplate(t.name)}
                    className={`w-full text-left px-4 py-3 hover:bg-[var(--bg)] transition-colors ${
                      selectedTemplate === t.name ? "bg-[var(--primary-light)] border-l-2 border-[var(--primary)]" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="text-sm font-medium text-[var(--text-primary)]">{t.name}</span>
                        {t.is_unlimited && (
                          <span className="ml-2 inline-flex rounded-full bg-[var(--success-light)] px-2 py-0.5 text-xs text-[var(--success)]">무제한</span>
                        )}
                      </div>
                      <span className="text-xs text-[var(--text-muted)]">{assignedCount}명</span>
                    </div>
                    <div className="mt-0.5 text-xs text-[var(--text-muted)]">
                      {t.is_unlimited
                        ? t.description || "무제한 사용"
                        : `${CYCLE_LABEL[t.refresh_cycle]} $${t.cost_limit_usd} (≈${Math.round(t.cost_limit_usd * krwRate).toLocaleString()}원)`}
                    </div>
                  </button>
                );
              })}
              <button
                onClick={startNewTemplate}
                className="w-full text-left px-4 py-3 text-sm text-[var(--primary)] hover:bg-[var(--primary-light)] transition-colors"
              >
                + 새 정책 만들기
              </button>
            </div>
          </div>

          {/* Template editor */}
          {(selectedTemplate || isCreatingNew) && (
            <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
              <div className="border-b border-[var(--border)] px-4 py-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                  {isCreatingNew ? "새 정책 만들기" : `"${selectedTemplate}" 수정`}
                </h2>
              </div>
              <div className="space-y-3 px-4 py-4">
                <div>
                  <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">정책 이름</label>
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="예: standard"
                    className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">설명</label>
                  <input
                    type="text"
                    value={editDesc}
                    onChange={(e) => setEditDesc(e.target.value)}
                    placeholder="정책 설명"
                    className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="unlimited"
                    checked={editUnlimited}
                    onChange={(e) => setEditUnlimited(e.target.checked)}
                    className="h-4 w-4 rounded border-[var(--border-strong)] text-[var(--primary)]"
                  />
                  <label htmlFor="unlimited" className="text-sm text-[var(--text-secondary)]">무제한 사용</label>
                </div>
                {!editUnlimited && (
                  <>
                    <div>
                      <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">비용 한도 (USD)</label>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          step="0.01"
                          value={editCostUsd}
                          onChange={(e) => setEditCostUsd(e.target.value)}
                          className="w-28 rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                        />
                        <span className="text-xs text-[var(--text-muted)]">
                          ≈ {Math.round((parseFloat(editCostUsd) || 0) * krwRate).toLocaleString()}원
                        </span>
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">초기화 주기</label>
                      <select
                        value={editCycle}
                        onChange={(e) => setEditCycle(e.target.value)}
                        className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                      >
                        {CYCLE_OPTIONS.map(o => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                    </div>
                  </>
                )}
                <div className="flex gap-2 pt-2">
                  <button
                    onClick={handleSaveTemplate}
                    className="rounded-md bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-white hover:bg-[var(--primary-hover)]"
                  >
                    {isCreatingNew ? "생성" : "저장"}
                  </button>
                  {!isCreatingNew && (
                    <button
                      onClick={handleDeleteTemplate}
                      className="rounded-md border border-[var(--danger)] px-4 py-1.5 text-sm font-medium text-[var(--danger)] hover:bg-[var(--danger-light)]"
                    >
                      삭제
                    </button>
                  )}
                  <button
                    onClick={() => { setSelectedTemplate(null); setIsCreatingNew(false); }}
                    className="rounded-md border border-[var(--border-strong)] px-4 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg)]"
                  >
                    취소
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
