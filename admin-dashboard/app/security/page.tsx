"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  getSecurityPolicies,
  getSecurityTemplates,
  getSecurityTables,
  getCustomTemplates,
  applySecurityTemplate,
  createCustomTemplate,
  updateCustomTemplate,
  deleteCustomTemplate,
  type SecurityPolicyWithUser,
  type SecurityLevel,
  type TableInfo,
  type CustomTemplate,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import StatsCard from "@/components/stats-card";
import Pagination from "@/components/pagination";

const REFRESH_INTERVAL = 30_000;

const KNOWN_LEVELS = ["basic", "standard", "full"] as const;

const LEVEL_BADGE: Record<string, string> = {
  basic: "bg-[var(--surface-hover)] text-[var(--text-secondary)]",
  standard: "bg-[var(--primary-light)] text-[var(--primary)]",
  full: "bg-[var(--success-light)] text-[var(--success)]",
};

const LEVEL_LABEL: Record<string, string> = {
  basic: "Basic",
  standard: "Standard",
  full: "Full",
};

const DB_KEYS = ["db_safety", "db_tango", "db_doculog"] as const;
const DB_LABELS: Record<string, string> = {
  db_safety: "Safety DB",
  db_tango: "Tango DB",
  db_doculog: "Docu-Log DB",
};

const SKILL_KEYS = ["db", "report", "excel", "share", "sms", "webapp"] as const;
const SKILL_LABELS: Record<string, string> = {
  db: "DB 조회",
  report: "리포트",
  excel: "엑셀",
  share: "공유",
  sms: "SMS",
  webapp: "웹앱",
};

/* ── Merged template type (built-in + custom) ── */
interface MergedTemplate {
  name: string;
  description: string;
  policy: Record<string, unknown>;
  isBuiltin: boolean;
  id?: number; // only for custom templates
}

/** Badge renderer -- purple fallback for custom levels */
function levelBadge(level: string) {
  const colors = LEVEL_BADGE[level] || "bg-[var(--info-light)] text-[var(--info)]";
  const label = LEVEL_LABEL[level] || level.charAt(0).toUpperCase() + level.slice(1);
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${colors}`}
    >
      {label}
    </span>
  );
}

export default function SecurityPage() {
  const router = useRouter();

  /* ── Left panel: user policies ── */
  const [policies, setPolicies] = useState<SecurityPolicyWithUser[]>([]);
  const [checkedUsers, setCheckedUsers] = useState<Set<number>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [policyPage, setPolicyPage] = useState(1);
  const PAGE_SIZE = 10;

  /* ── Right panel: template management ── */
  const [allTemplates, setAllTemplates] = useState<MergedTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [isCreatingNew, setIsCreatingNew] = useState(false);

  /* ── Template editing form (right detail) ── */
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editDbAccess, setEditDbAccess] = useState<
    Record<string, { allowed: boolean; tables: string[] }>
  >({
    db_safety: { allowed: false, tables: ["*"] },
    db_tango: { allowed: false, tables: ["*"] },
    db_doculog: { allowed: false, tables: ["*"] },
  });
  const [editSkills, setEditSkills] = useState<string[]>([]);
  const [editSchema, setEditSchema] = useState(true);
  const [editDirty, setEditDirty] = useState(false);

  /* ── General state ── */
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);

  /* ── Available tables from API ── */
  const [availableTables, setAvailableTables] = useState<{
    safety: TableInfo[];
    tango: TableInfo[];
    doculog: TableInfo[];
  }>({ safety: [], tango: [], doculog: [] });

  /* ── Table filter for search ── */
  const [tableFilter, setTableFilter] = useState("");

  /* ── Refs to prevent stale closures in interval ── */
  const editDirtyRef = useRef(editDirty);
  editDirtyRef.current = editDirty;

  /* ── Determine if the currently selected template is built-in ── */
  const selectedTemplateObj = allTemplates.find((t) => t.name === selectedTemplate);
  const isSelectedBuiltin = selectedTemplateObj?.isBuiltin ?? false;

  /* ── Load template data into the editing form ── */
  const loadTemplateIntoForm = useCallback((t: MergedTemplate) => {
    setEditName(t.name);
    setEditDesc(t.description);

    const dbAccess = t.policy?.db_access as
      | Record<string, { allowed?: boolean; tables?: string[] }>
      | undefined;

    setEditDbAccess({
      db_safety: {
        allowed: dbAccess?.safety?.allowed ?? false,
        tables: dbAccess?.safety?.tables ?? ["*"],
      },
      db_tango: {
        allowed: dbAccess?.tango?.allowed ?? false,
        tables: dbAccess?.tango?.tables ?? ["*"],
      },
      db_doculog: {
        allowed: dbAccess?.doculog?.allowed ?? false,
        tables: dbAccess?.doculog?.tables ?? ["*"],
      },
    });

    const allowedSkills = t.policy?.allowed_skills as string[] | undefined;
    if (!allowedSkills || allowedSkills.includes("*")) {
      setEditSkills([...SKILL_KEYS]);
    } else {
      setEditSkills(allowedSkills.filter((s) => SKILL_KEYS.includes(s as typeof SKILL_KEYS[number])));
    }

    setEditSchema((t.policy?.can_see_schema as boolean) ?? false);
    setTableFilter("");
    setEditDirty(false);
  }, []);

  /* ── Clear form for new template creation ── */
  const clearForm = useCallback(() => {
    setEditName("");
    setEditDesc("");
    setEditDbAccess({
      db_safety: { allowed: false, tables: ["*"] },
      db_tango: { allowed: false, tables: ["*"] },
      db_doculog: { allowed: false, tables: ["*"] },
    });
    setEditSkills([]);
    setEditSchema(false);
    setTableFilter("");
    setEditDirty(false);
  }, []);

  /* ── Build policy object from form state ── */
  function buildPolicy(): Record<string, unknown> {
    const allSkillsOn = editSkills.length === SKILL_KEYS.length;
    return {
      db_access: {
        safety: {
          allowed: editDbAccess.db_safety?.allowed ?? false,
          tables: editDbAccess.db_safety?.allowed ? (editDbAccess.db_safety?.tables ?? ["*"]) : [],
        },
        tango: {
          allowed: editDbAccess.db_tango?.allowed ?? false,
          tables: editDbAccess.db_tango?.allowed ? (editDbAccess.db_tango?.tables ?? ["*"]) : [],
        },
        doculog: {
          allowed: editDbAccess.db_doculog?.allowed ?? false,
          tables: editDbAccess.db_doculog?.allowed
            ? (editDbAccess.db_doculog?.tables ?? ["*"])
            : [],
        },
        platform: { allowed: false, tables: [] },
      },
      allowed_skills: allSkillsOn ? ["*"] : editSkills,
      can_see_schema: editSchema,
      restricted_topics: [],
    };
  }

  /* ── Data fetch -- NEVER overwrites form if editing ── */
  const fetchData = useCallback(async () => {
    try {
      const [policiesRes, templatesRes, customRes, tablesRes] = await Promise.all([
        getSecurityPolicies(),
        getSecurityTemplates(),
        getCustomTemplates().catch(() => ({ templates: [] as CustomTemplate[] })),
        getSecurityTables().catch(() => ({
          safety: [] as TableInfo[],
          tango: [] as TableInfo[],
          doculog: [] as TableInfo[],
        })),
      ]);

      setPolicies(policiesRes.policies);
      setAvailableTables(tablesRes);

      // Merge built-in + custom into allTemplates (custom overrides built-in with same name)
      const customMap = new Map(customRes.templates.map((t) => [t.name, t]));
      const builtIn: MergedTemplate[] = templatesRes.templates
        .filter((t) => !customMap.has(t.name))  // skip built-in if custom override exists
        .map((t) => ({
          name: t.name,
          description: t.description,
          policy: t.security_policy,
          isBuiltin: true,
        }));
      const custom: MergedTemplate[] = customRes.templates.map((t) => ({
        name: t.name,
        description: t.description,
        policy: t.policy,
        isBuiltin: false,
        id: t.id,
      }));
      setAllTemplates([...builtIn, ...custom]);

      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 조회 실패");
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
    const timer = setInterval(() => {
      // Don't overwrite form if user is editing
      if (!editDirtyRef.current) {
        fetchData();
      }
    }, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  /* ── Resolve which table list to use for a DB key ── */
  function getTablesForDb(dbKey: string): TableInfo[] {
    if (dbKey === "db_safety") return availableTables.safety;
    if (dbKey === "db_tango") return availableTables.tango;
    if (dbKey === "db_doculog") return availableTables.doculog;
    return [];
  }

  function clearMessages() {
    setError("");
    setSuccess("");
  }

  /* ── Stats ── */
  const basicCount = policies.filter((p) => p.security_level === "basic").length;
  const standardCount = policies.filter((p) => p.security_level === "standard").length;
  const fullCount = policies.filter((p) => p.security_level === "full").length;
  const customCount = policies.filter(
    (p) => !KNOWN_LEVELS.includes(p.security_level as (typeof KNOWN_LEVELS)[number]),
  ).length;

  /* ── Filtered policies for search ── */
  const filteredPolicies = policies.filter((p) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (p.name || "").toLowerCase().includes(q) ||
      p.username.toLowerCase().includes(q) ||
      (p.region_name || "").toLowerCase().includes(q) ||
      (p.team_name || "").toLowerCase().includes(q) ||
      p.security_level.toLowerCase().includes(q)
    );
  });

  // Reset page on search change
  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => { setPolicyPage(1); }, [searchQuery]);

  const policyTotalPages = Math.max(1, Math.ceil(filteredPolicies.length / PAGE_SIZE));
  const policySafePage = Math.min(policyPage, policyTotalPages);
  const paginatedPolicies = filteredPolicies.slice((policySafePage - 1) * PAGE_SIZE, policySafePage * PAGE_SIZE);

  /* ── Left panel: quick dropdown per user ── */
  async function handleQuickApply(userId: number, templateName: string) {
    if (!templateName) return;
    clearMessages();
    try {
      await applySecurityTemplate(userId, templateName as SecurityLevel);
      setSuccess(`"${templateName}" 정책이 적용되었습니다.`);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "정책 적용 실패");
    }
  }

  /* ── Left panel: bulk apply ── */
  async function handleBulkApply() {
    const selectEl = document.getElementById("bulkPolicy") as HTMLSelectElement | null;
    if (!selectEl) return;
    const policyName = selectEl.value;
    if (!policyName) return;
    clearMessages();

    const count = checkedUsers.size;
    if (!window.confirm(`${count}명에게 "${policyName}" 정책을 일괄 적용하시겠습니까?`)) return;

    try {
      for (const userId of checkedUsers) {
        await applySecurityTemplate(userId, policyName as SecurityLevel);
      }
      setCheckedUsers(new Set());
      setSuccess(`${count}명에게 "${policyName}" 정책이 적용되었습니다.`);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "일괄 적용 실패");
    }
  }

  /* ── Right panel: save template ── */
  async function handleSaveTemplate() {
    clearMessages();
    setSaving(true);
    try {
      const policy = buildPolicy();

      if (isCreatingNew) {
        if (!editName.trim()) {
          setError("정책 이름을 입력하세요.");
          setSaving(false);
          return;
        }
        await createCustomTemplate({
          name: editName.trim(),
          description: editDesc.trim(),
          policy: { ...policy, security_level: editName.trim() },
        });
        setSuccess(`커스텀 정책 "${editName.trim()}"이(가) 생성되었습니다.`);
        setIsCreatingNew(false);
      } else if (selectedTemplateObj && !selectedTemplateObj.isBuiltin) {
        await updateCustomTemplate(selectedTemplateObj.id!, {
          name: editName.trim(),
          description: editDesc.trim(),
          policy: { ...policy, security_level: editName.trim() },
        });
        setSuccess(`정책 "${editName.trim()}"이(가) 수정되었습니다.`);
      } else if (selectedTemplateObj && selectedTemplateObj.isBuiltin) {
        // Built-in template edited: save as a custom template override
        // Check if a custom override already exists
        const existingCustom = allTemplates.find(
          (t) => !t.isBuiltin && t.name === selectedTemplateObj.name,
        );
        if (existingCustom?.id) {
          await updateCustomTemplate(existingCustom.id, {
            name: selectedTemplateObj.name,
            description: editDesc.trim(),
            policy: { ...policy, security_level: selectedTemplateObj.name },
          });
        } else {
          await createCustomTemplate({
            name: selectedTemplateObj.name,
            description: editDesc.trim(),
            policy: { ...policy, security_level: selectedTemplateObj.name },
          });
        }
        setSuccess(`기본 정책 "${selectedTemplateObj.name}"이(가) 커스텀 오버라이드로 저장되었습니다.`);
      }

      setEditDirty(false);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  }

  /* ── Right panel: delete custom template ── */
  async function handleDeleteTemplate() {
    if (!selectedTemplateObj || selectedTemplateObj.isBuiltin || !selectedTemplateObj.id) return;
    if (
      !window.confirm(
        `정책 "${selectedTemplateObj.name}"을(를) 삭제하시겠습니까? 이 정책을 사용 중인 사용자는 영향을 받을 수 있습니다.`,
      )
    )
      return;

    clearMessages();
    try {
      await deleteCustomTemplate(selectedTemplateObj.id);
      setSuccess(`정책 "${selectedTemplateObj.name}"이(가) 삭제되었습니다.`);
      setSelectedTemplate(null);
      setIsCreatingNew(false);
      clearForm();
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "삭제 실패");
    }
  }

  /* ── Checkbox helpers ── */
  function toggleUserCheck(userId: number) {
    setCheckedUsers((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  function toggleAllUsers() {
    if (checkedUsers.size === filteredPolicies.length) {
      setCheckedUsers(new Set());
    } else {
      setCheckedUsers(new Set(filteredPolicies.map((p) => p.user_id)));
    }
  }

  /* ── Table toggle helper ── */
  function toggleTable(dbKey: string, tableName: string, checked: boolean) {
    setEditDbAccess((prev) => {
      const current = prev[dbKey];
      if (!current) return prev;
      const tables = current.tables ?? [];
      return {
        ...prev,
        [dbKey]: {
          ...current,
          tables: checked ? [...tables, tableName] : tables.filter((t) => t !== tableName),
        },
      };
    });
    setEditDirty(true);
  }

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {/* Messages */}
        {error && (
          <div className="mb-6 rounded-md bg-[var(--error-light)] px-4 py-3 text-sm text-[var(--error)]">{error}</div>
        )}
        {success && (
          <div className="mb-6 rounded-md bg-[var(--success-light)] px-4 py-3 text-sm text-[var(--success)]">
            {success}
          </div>
        )}

        {/* Stats Cards -- full width */}
        <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatsCard label="Basic" value={basicCount} />
          <StatsCard label="Standard" value={standardCount} />
          <StatsCard label="Full" value={fullCount} />
          <StatsCard label="Custom" value={customCount} />
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
            데이터를 불러오는 중...
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
            {/* ═══════════════════════════════════════
                LEFT PANEL (60%) -- User list + assignment
                ═══════════════════════════════════════ */}
            <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm lg:col-span-3">
              <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                  사용자 보안 정책 ({policies.length}명)
                </h2>
                <span className="text-xs text-[var(--text-muted)]">30초마다 자동 갱신</span>
              </div>

              {/* Search + Bulk action toolbar */}
              <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
                {/* Left: search */}
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="사용자 검색 (이름, 사번, 소속)..."
                    className="rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm w-64 focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                  {searchQuery && (
                    <span className="text-xs text-[var(--text-muted)]">{filteredPolicies.length}건</span>
                  )}
                </div>

                {/* Right: bulk action (visible when users are checked) */}
                {checkedUsers.size > 0 && (
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-[var(--primary)]">{checkedUsers.size}명 선택</span>
                    <select
                      id="bulkPolicy"
                      className="rounded-md border border-[var(--border-strong)] px-2 py-1.5 text-sm shadow-sm focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                    >
                      {allTemplates.map((t) => (
                        <option key={t.name} value={t.name}>
                          {t.name}
                                                  </option>
                      ))}
                    </select>
                    <button
                      onClick={handleBulkApply}
                      className="rounded-md bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-[var(--primary-hover)] transition-colors"
                    >
                      일괄 적용
                    </button>
                    <button
                      onClick={() => setCheckedUsers(new Set())}
                      className="text-sm text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                    >
                      선택 해제
                    </button>
                  </div>
                )}
              </div>

              {filteredPolicies.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
                  {searchQuery ? "검색 결과가 없습니다." : "사용자가 없습니다."}
                </div>
              ) : (
                <>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-[var(--border)]">
                    <thead className="bg-[var(--bg)]">
                      <tr>
                        <th className="w-10 px-4 py-3">
                          <input
                            type="checkbox"
                            checked={
                              filteredPolicies.length > 0 && checkedUsers.size === filteredPolicies.length
                            }
                            onChange={toggleAllUsers}
                            className="h-4 w-4 rounded border-[var(--border-strong)] text-[var(--primary)] focus:ring-[var(--primary)]"
                          />
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          사용자
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          소속
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          보안등급
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          빠른설정
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                      {paginatedPolicies.map((p) => (
                        <tr key={p.user_id} className="hover:bg-[var(--bg)]">
                          <td className="px-4 py-3">
                            <input
                              type="checkbox"
                              checked={checkedUsers.has(p.user_id)}
                              onChange={() => toggleUserCheck(p.user_id)}
                              className="h-4 w-4 rounded border-[var(--border-strong)] text-[var(--primary)] focus:ring-[var(--primary)]"
                            />
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                            {p.name ?? p.username}
                            <span className="ml-1 text-xs text-[var(--text-muted)]">({p.username})</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                            {p.region_name ?? "-"}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            {levelBadge(p.security_level)}
                            {p.pod_restart_required && (
                              <span className="ml-1.5 inline-flex items-center rounded-full bg-[var(--warning-light)] px-2 py-0.5 text-xs font-medium text-[var(--warning)]">
                                재시작 필요
                              </span>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <select
                              value=""
                              onChange={(e) => {
                                if (!e.target.value) return;
                                handleQuickApply(p.user_id, e.target.value);
                                e.target.value = "";
                              }}
                              className="rounded-md border border-[var(--border-strong)] px-2 py-1 text-sm shadow-sm focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                            >
                              <option value="">
                                현재:{" "}
                                {LEVEL_LABEL[p.security_level] ?? p.security_level}
                              </option>
                              {allTemplates.map((t) => (
                                <option key={t.name} value={t.name}>
                                  {t.name}
                                                                  </option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  currentPage={policySafePage}
                  totalPages={policyTotalPages}
                  totalItems={filteredPolicies.length}
                  itemsPerPage={PAGE_SIZE}
                  onPageChange={setPolicyPage}
                />
                </>
              )}

            </div>

            {/* ═══════════════════════════════════════
                RIGHT PANEL (40%) -- Policy management
                ═══════════════════════════════════════ */}
            <div className="space-y-4 lg:col-span-2">
              {/* ── Template list ── */}
              <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                <div className="border-b border-[var(--border)] px-4 py-3">
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">정책 관리</h2>
                </div>
                <div className="max-h-72 overflow-y-auto p-3 space-y-1">
                  {allTemplates.map((t) => {
                    const isActive =
                      !isCreatingNew && selectedTemplate === t.name;
                    const usersOnPolicy = policies.filter(
                      (p) => p.security_level === t.name,
                    ).length;

                    return (
                      <div
                        key={t.name}
                        onClick={() => {
                          if (editDirty && !window.confirm("저장되지 않은 변경이 있습니다. 계속하시겠습니까?")) return;
                          setSelectedTemplate(t.name);
                          setIsCreatingNew(false);
                          loadTemplateIntoForm(t);
                        }}
                        className={`flex items-center justify-between p-2.5 rounded-md cursor-pointer text-sm transition-colors ${
                          isActive
                            ? "bg-[var(--primary-light)] border border-[var(--primary)]"
                            : "hover:bg-[var(--bg)] border border-transparent"
                        } ${!t.isBuiltin ? "border-l-4 border-l-purple-300" : ""}`}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-[var(--text-primary)] truncate">
                            {t.name}
                            {t.isBuiltin && (
                              <span className="ml-1.5 text-xs text-[var(--text-muted)] font-normal">
                                기본
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-[var(--text-muted)] truncate">
                            {t.description || (t.isBuiltin ? "기본 정책" : "Custom")}
                          </div>
                        </div>
                        {usersOnPolicy > 0 && (
                          <span className="ml-2 flex-shrink-0 inline-flex items-center rounded-full bg-[var(--surface-hover)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
                            {usersOnPolicy}명
                          </span>
                        )}
                      </div>
                    );
                  })}

                  {/* New policy button */}
                  <div
                    onClick={() => {
                      if (editDirty && !window.confirm("저장되지 않은 변경이 있습니다. 계속하시겠습니까?")) return;
                      setIsCreatingNew(true);
                      setSelectedTemplate(null);
                      clearForm();
                    }}
                    className="p-2.5 rounded-md cursor-pointer text-sm text-[var(--info)] hover:bg-[var(--info-light)] border border-dashed border-[var(--primary)] transition-colors"
                  >
                    + 새 정책 만들기
                  </div>
                </div>
              </div>

              {/* ── Selected template detail ── */}
              {(selectedTemplate !== null || isCreatingNew) && (
                <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                  <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                      {isCreatingNew
                        ? "새 정책 만들기"
                        : `${selectedTemplate} 편집`}
                    </h3>
                    {editDirty && (
                      <span className="text-xs font-medium text-[var(--warning)]">변경됨</span>
                    )}
                  </div>

                  <div className="space-y-4 px-4 py-4">
                    {/* Policy name */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                        정책명
                      </label>
                      {isCreatingNew ? (
                        <input
                          type="text"
                          placeholder="예: 경영진전용"
                          value={editName}
                          onChange={(e) => {
                            setEditName(e.target.value);
                            setEditDirty(true);
                          }}
                          className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                        />
                      ) : (
                        <p className="text-sm text-[var(--text-primary)] font-medium">{editName}</p>
                      )}
                    </div>

                    {/* Description */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                        설명
                      </label>
                      <input
                        type="text"
                        placeholder="정책 설명"
                        value={editDesc}
                        onChange={(e) => {
                          setEditDesc(e.target.value);
                          setEditDirty(true);
                        }}
                        className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                      />
                    </div>

                    {/* DB Access */}
                    <fieldset>
                      <legend className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-2">
                        DB 접근 권한
                      </legend>
                      <div className="space-y-2">
                        {DB_KEYS.map((key) => {
                          const dbState = editDbAccess[key];
                          const isAllowed = dbState?.allowed ?? false;
                          const tables = dbState?.tables ?? ["*"];
                          const isAll = tables.length === 1 && tables[0] === "*";

                          return (
                            <div key={key}>
                              <div className="flex items-center justify-between">
                                <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                                  <input
                                    type="checkbox"
                                    checked={isAllowed}

                                    onChange={(e) => {
                                      setEditDbAccess((prev) => ({
                                        ...prev,
                                        [key]: { ...prev[key], allowed: e.target.checked },
                                      }));
                                      setEditDirty(true);
                                    }}
                                    className="h-4 w-4 rounded border-[var(--border-strong)] text-[var(--primary)] focus:ring-[var(--primary)]"
                                  />
                                  {DB_LABELS[key]}
                                </label>
                                {isAllowed && (
                                  <select
                                    value={isAll ? "all" : "custom"}

                                    onChange={(e) => {
                                      setEditDbAccess((prev) => ({
                                        ...prev,
                                        [key]: {
                                          ...prev[key],
                                          tables: e.target.value === "all" ? ["*"] : [],
                                        },
                                      }));
                                      setEditDirty(true);
                                    }}
                                    className="text-xs border rounded px-1.5 py-0.5"
                                  >
                                    <option value="all">전체</option>
                                    <option value="custom">선택...</option>
                                  </select>
                                )}
                              </div>

                              {/* Table-level selector */}
                              {isAllowed && !isAll && (
                                <div className="ml-6 mt-2 max-h-36 overflow-y-auto border rounded p-2 space-y-1">
                                  <input
                                    type="text"
                                    placeholder="테이블 검색..."
                                    className="w-full text-xs border-b pb-1 mb-1 px-1 outline-none"
                                    value={tableFilter}
                                    onChange={(e) => setTableFilter(e.target.value)}
                                  />
                                  {getTablesForDb(key).length === 0 ? (
                                    <p className="text-xs text-[var(--text-muted)] py-1">
                                      테이블 목록을 불러올 수 없습니다
                                    </p>
                                  ) : (
                                    getTablesForDb(key)
                                      .filter(
                                        (t) =>
                                          !tableFilter ||
                                          t.name.toLowerCase().includes(tableFilter.toLowerCase()) ||
                                          t.description
                                            .toLowerCase()
                                            .includes(tableFilter.toLowerCase()),
                                      )
                                      .map((t) => (
                                        <label
                                          key={t.name}
                                          className="flex items-center gap-2 text-xs hover:bg-[var(--bg)] px-1 py-0.5 rounded"
                                        >
                                          <input
                                            type="checkbox"
                                            checked={tables.includes(t.name)}
        
                                            onChange={(e) =>
                                              toggleTable(key, t.name, e.target.checked)
                                            }
                                            className="h-3 w-3 rounded border-[var(--border-strong)] text-[var(--primary)]"
                                          />
                                          <span className="font-mono text-[var(--text-secondary)]">
                                            {t.name}
                                          </span>
                                          {t.description && (
                                            <span className="text-[var(--text-muted)]">
                                              -- {t.description}
                                            </span>
                                          )}
                                        </label>
                                      ))
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}

                        <label className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
                          <input
                            type="checkbox"
                            checked={false}
                            disabled
                            className="h-4 w-4 rounded border-[var(--border-strong)]"
                          />
                          Platform DB (항상 OFF)
                        </label>
                      </div>
                    </fieldset>

                    {/* Skills */}
                    <fieldset>
                      <legend className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-2">
                        허용 스킬
                      </legend>
                      <div className="grid grid-cols-2 gap-2">
                        {SKILL_KEYS.map((key) => (
                          <label
                            key={key}
                            className="flex items-center gap-2 text-sm text-[var(--text-secondary)]"
                          >
                            <input
                              type="checkbox"
                              checked={editSkills.includes(key)}
                              disabled={false}
                              onChange={(e) => {
                                setEditSkills((prev) =>
                                  e.target.checked
                                    ? [...prev, key]
                                    : prev.filter((s) => s !== key),
                                );
                                setEditDirty(true);
                              }}
                              className="h-4 w-4 rounded border-[var(--border-strong)] text-[var(--primary)] focus:ring-[var(--primary)]"
                            />
                            {SKILL_LABELS[key]}
                          </label>
                        ))}
                      </div>
                    </fieldset>

                    {/* Schema exposure */}
                    <fieldset>
                      <legend className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-2">
                        스키마 노출
                      </legend>
                      <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                        <input
                          type="checkbox"
                          checked={editSchema}
                          disabled={false}
                          onChange={(e) => {
                            setEditSchema(e.target.checked);
                            setEditDirty(true);
                          }}
                          className="h-4 w-4 rounded border-[var(--border-strong)] text-[var(--primary)] focus:ring-[var(--primary)]"
                        />
                        DB 스키마 정보 제공
                      </label>
                    </fieldset>

                    {/* Action Buttons */}
                    <div className="flex gap-2 pt-2">
                      <button
                        onClick={handleSaveTemplate}
                        disabled={saving}
                        className="flex-1 rounded-md bg-[var(--primary)] px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-[var(--primary-hover)] disabled:opacity-50 transition-colors"
                      >
                        {saving ? "저장 중..." : isCreatingNew ? "생성" : "저장"}
                      </button>
                      {!isCreatingNew && selectedTemplateObj && !isSelectedBuiltin && (
                        <button
                          onClick={handleDeleteTemplate}
                          className="rounded-md border border-[var(--danger)] px-3 py-2 text-sm font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] transition-colors"
                        >
                          삭제
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </>
  );
}
