"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
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
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import StatsCard from "@/components/stats-card";

const REFRESH_INTERVAL = 30_000;

const KNOWN_LEVELS = ["basic", "standard", "full"] as const;

const LEVEL_BADGE: Record<string, string> = {
  basic: "bg-gray-100 text-gray-600",
  standard: "bg-blue-100 text-blue-700",
  full: "bg-green-100 text-green-700",
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
  const colors = LEVEL_BADGE[level] || "bg-purple-100 text-purple-700";
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
  const user = getUser();
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
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
          <h1 className="text-lg font-bold text-gray-900">Claude Code Admin</h1>
          <div className="flex items-center gap-6">
            <nav className="flex gap-4 text-sm font-medium text-gray-600">
              <Link href="/dashboard" className="hover:text-gray-900 transition-colors">
                운용현황
              </Link>
              <Link href="/users" className="hover:text-gray-900 transition-colors">
                사용자 관리
              </Link>
              <Link
                href="/security"
                className="text-blue-600 border-b-2 border-blue-600 pb-0.5"
              >
                보안 정책
              </Link>
              <Link href="/usage" className="hover:text-gray-900 transition-colors">
                토큰 사용량
              </Link>
              <Link href="/infra" className="hover:text-gray-900 transition-colors">
                인프라
              </Link>
            </nav>
            <div className="flex items-center gap-3">
              {user && <span className="text-sm text-gray-500">{user.name}</span>}
              <button
                onClick={logout}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
              >
                로그아웃
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {/* Messages */}
        {error && (
          <div className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}
        {success && (
          <div className="mb-6 rounded-md bg-green-50 px-4 py-3 text-sm text-green-700">
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
          <div className="flex items-center justify-center py-12 text-gray-400">
            데이터를 불러오는 중...
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
            {/* ═══════════════════════════════════════
                LEFT PANEL (60%) -- User list + assignment
                ═══════════════════════════════════════ */}
            <div className="rounded-lg border border-gray-200 bg-white shadow-sm lg:col-span-3">
              <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                <h2 className="text-sm font-semibold text-gray-900">
                  사용자 보안 정책 ({policies.length}명)
                </h2>
                <span className="text-xs text-gray-400">30초마다 자동 갱신</span>
              </div>

              {/* Search + Bulk action toolbar */}
              <div className="flex items-center justify-between gap-3 border-b border-gray-200 px-4 py-3">
                {/* Left: search */}
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="사용자 검색 (이름, 사번, 소속)..."
                    className="rounded-md border border-gray-300 px-3 py-1.5 text-sm w-64 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  {searchQuery && (
                    <span className="text-xs text-gray-400">{filteredPolicies.length}건</span>
                  )}
                </div>

                {/* Right: bulk action (visible when users are checked) */}
                {checkedUsers.size > 0 && (
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-blue-600">{checkedUsers.size}명 선택</span>
                    <select
                      id="bulkPolicy"
                      className="rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    >
                      {allTemplates.map((t) => (
                        <option key={t.name} value={t.name}>
                          {t.name}
                          {t.isBuiltin ? "" : " (custom)"}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={handleBulkApply}
                      className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700 transition-colors"
                    >
                      일괄 적용
                    </button>
                    <button
                      onClick={() => setCheckedUsers(new Set())}
                      className="text-sm text-gray-500 hover:text-gray-700"
                    >
                      선택 해제
                    </button>
                  </div>
                )}
              </div>

              {filteredPolicies.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-gray-400">
                  {searchQuery ? "검색 결과가 없습니다." : "사용자가 없습니다."}
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="w-10 px-4 py-3">
                          <input
                            type="checkbox"
                            checked={
                              filteredPolicies.length > 0 && checkedUsers.size === filteredPolicies.length
                            }
                            onChange={toggleAllUsers}
                            className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                          />
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          사용자
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          소속
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          보안등급
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          빠른설정
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200 bg-white">
                      {filteredPolicies.map((p) => (
                        <tr key={p.user_id} className="hover:bg-gray-50">
                          <td className="px-4 py-3">
                            <input
                              type="checkbox"
                              checked={checkedUsers.has(p.user_id)}
                              onChange={() => toggleUserCheck(p.user_id)}
                              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                            />
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                            {p.name ?? p.username}
                            <span className="ml-1 text-xs text-gray-400">({p.username})</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                            {p.region_name ?? "-"}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            {levelBadge(p.security_level)}
                            {p.pod_restart_required && (
                              <span className="ml-1.5 inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-700">
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
                              className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              <option value="">
                                현재:{" "}
                                {LEVEL_LABEL[p.security_level] ?? p.security_level}
                              </option>
                              {allTemplates.map((t) => (
                                <option key={t.name} value={t.name}>
                                  {t.name}
                                  {t.isBuiltin ? "" : " (custom)"}
                                </option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

            </div>

            {/* ═══════════════════════════════════════
                RIGHT PANEL (40%) -- Policy management
                ═══════════════════════════════════════ */}
            <div className="space-y-4 lg:col-span-2">
              {/* ── Template list ── */}
              <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
                <div className="border-b border-gray-200 px-4 py-3">
                  <h2 className="text-sm font-semibold text-gray-900">정책 관리</h2>
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
                            ? "bg-blue-50 border border-blue-300"
                            : "hover:bg-gray-50 border border-transparent"
                        } ${!t.isBuiltin ? "border-l-4 border-l-purple-300" : ""}`}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-gray-900 truncate">
                            {t.name}
                            {t.isBuiltin && (
                              <span className="ml-1.5 text-xs text-gray-400 font-normal">
                                기본
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-gray-400 truncate">
                            {t.description || (t.isBuiltin ? "기본 정책" : "Custom")}
                          </div>
                        </div>
                        {usersOnPolicy > 0 && (
                          <span className="ml-2 flex-shrink-0 inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
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
                    className="p-2.5 rounded-md cursor-pointer text-sm text-purple-600 hover:bg-purple-50 border border-dashed border-purple-200 transition-colors"
                  >
                    + 새 정책 만들기
                  </div>
                </div>
              </div>

              {/* ── Selected template detail ── */}
              {(selectedTemplate !== null || isCreatingNew) && (
                <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
                  <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                    <h3 className="text-sm font-semibold text-gray-900">
                      {isCreatingNew
                        ? "새 정책 만들기"
                        : `${selectedTemplate} 편집`}
                    </h3>
                    {editDirty && (
                      <span className="text-xs font-medium text-amber-600">변경됨</span>
                    )}
                  </div>

                  <div className="space-y-4 px-4 py-4">
                    {/* Policy name */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
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
                          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                        />
                      ) : (
                        <p className="text-sm text-gray-900 font-medium">{editName}</p>
                      )}
                    </div>

                    {/* Description */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
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
                        className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    </div>

                    {/* DB Access */}
                    <fieldset>
                      <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
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
                                <label className="flex items-center gap-2 text-sm text-gray-700">
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
                                    className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
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
                                    <p className="text-xs text-gray-400 py-1">
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
                                          className="flex items-center gap-2 text-xs hover:bg-gray-50 px-1 py-0.5 rounded"
                                        >
                                          <input
                                            type="checkbox"
                                            checked={tables.includes(t.name)}
        
                                            onChange={(e) =>
                                              toggleTable(key, t.name, e.target.checked)
                                            }
                                            className="h-3 w-3 rounded border-gray-300 text-blue-600"
                                          />
                                          <span className="font-mono text-gray-700">
                                            {t.name}
                                          </span>
                                          {t.description && (
                                            <span className="text-gray-400">
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

                        <label className="flex items-center gap-2 text-sm text-gray-400">
                          <input
                            type="checkbox"
                            checked={false}
                            disabled
                            className="h-4 w-4 rounded border-gray-300"
                          />
                          Platform DB (항상 OFF)
                        </label>
                      </div>
                    </fieldset>

                    {/* Skills */}
                    <fieldset>
                      <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                        허용 스킬
                      </legend>
                      <div className="grid grid-cols-2 gap-2">
                        {SKILL_KEYS.map((key) => (
                          <label
                            key={key}
                            className="flex items-center gap-2 text-sm text-gray-700"
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
                              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                            />
                            {SKILL_LABELS[key]}
                          </label>
                        ))}
                      </div>
                    </fieldset>

                    {/* Schema exposure */}
                    <fieldset>
                      <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                        스키마 노출
                      </legend>
                      <label className="flex items-center gap-2 text-sm text-gray-700">
                        <input
                          type="checkbox"
                          checked={editSchema}
                          disabled={false}
                          onChange={(e) => {
                            setEditSchema(e.target.checked);
                            setEditDirty(true);
                          }}
                          className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                        />
                        DB 스키마 정보 제공
                      </label>
                    </fieldset>

                    {/* Action Buttons */}
                    <div className="flex gap-2 pt-2">
                      <button
                        onClick={handleSaveTemplate}
                        disabled={saving}
                        className="flex-1 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
                      >
                        {saving ? "저장 중..." : isCreatingNew ? "생성" : "저장"}
                      </button>
                      {!isCreatingNew && selectedTemplateObj && !isSelectedBuiltin && (
                        <button
                          onClick={handleDeleteTemplate}
                          className="rounded-md border border-red-300 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 transition-colors"
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
    </div>
  );
}
