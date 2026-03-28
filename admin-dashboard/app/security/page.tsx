"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  getSecurityPolicies,
  getSecurityTables,
  applySecurityTemplate,
  updateSecurityPolicy,
  getCustomTemplates,
  createCustomTemplate,
  updateCustomTemplate,
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

const BUILTIN_LEVEL_OPTIONS: {
  value: string;
  label: string;
  desc: string;
  descClass: string;
}[] = [
  { value: "basic", label: "Basic", desc: "스킬만 사용, DB 직접접근 불가", descClass: "text-xs text-gray-400" },
  { value: "standard", label: "Standard", desc: "허용 DB/테이블 접근, 전체 스킬", descClass: "text-xs text-gray-400" },
  { value: "full", label: "Full", desc: "전체 접근 (주의: 모든 데이터 열람 가능)", descClass: "text-xs text-amber-500" },
];

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

/* ── Level template defaults ── */
const LEVEL_DEFAULTS: Record<
  string,
  {
    db_safety: boolean;
    db_tango: boolean;
    db_doculog: boolean;
    skills: string[];
    can_see_schema: boolean;
  }
> = {
  basic: {
    db_safety: false,
    db_tango: false,
    db_doculog: false,
    skills: ["report", "share"],
    can_see_schema: false,
  },
  standard: {
    db_safety: true,
    db_tango: true,
    db_doculog: true,
    skills: ["db", "report", "excel", "share", "sms", "webapp"],
    can_see_schema: true,
  },
  full: {
    db_safety: true,
    db_tango: true,
    db_doculog: true,
    skills: ["db", "report", "excel", "share", "sms", "webapp"],
    can_see_schema: true,
  },
};

/** Badge renderer — purple fallback for custom levels */
function levelBadge(level: string) {
  const colors = LEVEL_BADGE[level] || "bg-purple-100 text-purple-700";
  const label = LEVEL_LABEL[level] || level.charAt(0).toUpperCase() + level.slice(1);
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${colors}`}>
      {label}
    </span>
  );
}

export default function SecurityPage() {
  const router = useRouter();
  const [policies, setPolicies] = useState<SecurityPolicyWithUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

  // Form dirty tracking — prevents auto-refresh from wiping unsaved edits
  const [formDirty, setFormDirty] = useState(false);

  // Detail panel local state (form state, independent of policies)
  const [detailLevel, setDetailLevel] = useState<string>("basic");
  const [selectedRadio, setSelectedRadio] = useState<string>("basic");
  const [customLevelName, setCustomLevelName] = useState("");
  const [detailDbAccess, setDetailDbAccess] = useState<Record<string, boolean>>({});
  const [detailDbTables, setDetailDbTables] = useState<Record<string, string[]>>({
    db_safety: ["*"],
    db_tango: ["*"],
    db_doculog: ["*"],
  });
  const [detailSkills, setDetailSkills] = useState<Record<string, boolean>>({});
  const [detailSchemaExposure, setDetailSchemaExposure] = useState(false);
  const [saving, setSaving] = useState(false);

  // Available tables from API
  const [availableTables, setAvailableTables] = useState<{ safety: TableInfo[]; tango: TableInfo[]; doculog: TableInfo[] }>({
    safety: [],
    tango: [],
    doculog: [],
  });

  // Custom templates
  const [customTemplates, setCustomTemplates] = useState<CustomTemplate[]>([]);
  // When saving with a custom template selected, also update the template definition
  const [updateTemplateOnSave, setUpdateTemplateOnSave] = useState(false);

  // Table filter for search
  const [tableFilter, setTableFilter] = useState("");

  // Ref to track formDirty in the interval callback without re-creating it
  const formDirtyRef = useRef(formDirty);
  formDirtyRef.current = formDirty;

  const selectedUserIdRef = useRef(selectedUserId);
  selectedUserIdRef.current = selectedUserId;

  /* ── Populate form fields from a policy object ── */
  const initFormFromPolicy = useCallback((p: SecurityPolicyWithUser) => {
    const level = p.security_level;
    const isBuiltin = KNOWN_LEVELS.includes(level as typeof KNOWN_LEVELS[number]);

    // If the level matches a built-in, select that radio; otherwise select the custom template name directly
    setSelectedRadio(isBuiltin ? level : level);
    setDetailLevel(level);
    setCustomLevelName(isBuiltin ? "" : level);
    setUpdateTemplateOnSave(false);

    // Backend stores db access in nested structure: db_access.safety.allowed / .tables
    const dbAccess = (p.security_policy as Record<string, unknown>)?.db_access as
      | Record<string, { allowed?: boolean; tables?: string[] }>
      | undefined;

    setDetailDbAccess({
      db_safety: dbAccess?.safety?.allowed ?? false,
      db_tango: dbAccess?.tango?.allowed ?? false,
      db_doculog: dbAccess?.doculog?.allowed ?? false,
    });
    setDetailDbTables({
      db_safety: dbAccess?.safety?.tables ?? ["*"],
      db_tango: dbAccess?.tango?.tables ?? ["*"],
      db_doculog: dbAccess?.doculog?.tables ?? ["*"],
    });

    // Skills: backend stores allowed_skills as string[] (["*"] or ["db","report",...])
    const allowedSkills = (p.security_policy as Record<string, unknown>)?.allowed_skills as
      | string[]
      | undefined;
    const skills: Record<string, boolean> = {};
    const isAllSkills = !allowedSkills || allowedSkills.includes("*");
    for (const k of SKILL_KEYS) {
      skills[k] = isAllSkills || allowedSkills!.includes(k);
    }
    setDetailSkills(skills);

    setDetailSchemaExposure(
      ((p.security_policy as Record<string, unknown>)?.can_see_schema as boolean) ?? false,
    );
    setTableFilter("");
  }, []);

  /* ── Populate form fields from a raw policy object (e.g. custom template) ── */
  const initFormFromTemplatePolicy = useCallback((policy: Record<string, unknown>) => {
    const dbAccess = policy?.db_access as
      | Record<string, { allowed?: boolean; tables?: string[] }>
      | undefined;

    setDetailDbAccess({
      db_safety: dbAccess?.safety?.allowed ?? false,
      db_tango: dbAccess?.tango?.allowed ?? false,
      db_doculog: dbAccess?.doculog?.allowed ?? false,
    });
    setDetailDbTables({
      db_safety: dbAccess?.safety?.tables ?? ["*"],
      db_tango: dbAccess?.tango?.tables ?? ["*"],
      db_doculog: dbAccess?.doculog?.tables ?? ["*"],
    });

    const allowedSkills = policy?.allowed_skills as string[] | undefined;
    const skills: Record<string, boolean> = {};
    const isAllSkills = !allowedSkills || allowedSkills.includes("*");
    for (const k of SKILL_KEYS) {
      skills[k] = isAllSkills || allowedSkills!.includes(k);
    }
    setDetailSkills(skills);

    setDetailSchemaExposure((policy?.can_see_schema as boolean) ?? false);
    setTableFilter("");
  }, []);

  /* ── Data fetch — NEVER touches form state ── */
  const fetchData = useCallback(async () => {
    try {
      const [policyRes, tablesRes, ctRes] = await Promise.all([
        getSecurityPolicies(),
        getSecurityTables().catch(() => ({ safety: [], tango: [], doculog: [] })),
        getCustomTemplates().catch(() => ({ templates: [] })),
      ]);
      setPolicies(policyRes.policies);
      setAvailableTables(tablesRes);
      setCustomTemplates(ctRes.templates);

      // If a user is selected AND form is NOT dirty, silently sync form from fresh data
      const currentSelectedId = selectedUserIdRef.current;
      if (currentSelectedId !== null && !formDirtyRef.current) {
        const freshPolicy = policyRes.policies.find((p) => p.user_id === currentSelectedId);
        if (freshPolicy) {
          initFormFromPolicy(freshPolicy);
        }
      }
      // If formDirty is true, we leave form state untouched

      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 조회 실패");
    } finally {
      setLoading(false);
    }
  }, [initFormFromPolicy]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    fetchData();
    const timer = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  /* ── Row click handler with dirty-check ── */
  function handleSelectUser(userId: number) {
    if (userId === selectedUserId) return; // already selected
    if (formDirty && !window.confirm("저장되지 않은 변경사항이 있습니다. 계속하시겠습니까?")) return;
    setSelectedUserId(userId);
    const policy = policies.find((p) => p.user_id === userId);
    if (policy) {
      initFormFromPolicy(policy);
    }
    setFormDirty(false);
  }

  const user = getUser();
  const selectedPolicy = policies.find((u) => u.user_id === selectedUserId) ?? null;

  const basicCount = policies.filter((p) => p.security_level === "basic").length;
  const standardCount = policies.filter((p) => p.security_level === "standard").length;
  const fullCount = policies.filter((p) => p.security_level === "full").length;
  const customCount = policies.filter(
    (p) => !KNOWN_LEVELS.includes(p.security_level as typeof KNOWN_LEVELS[number])
  ).length;

  function clearMessages() {
    setError("");
    setSuccess("");
  }

  /* ── Auto-fill form when security level radio changes ── */
  function handleLevelChange(radio: string) {
    setSelectedRadio(radio);
    setFormDirty(true);
    setUpdateTemplateOnSave(false);

    // "Create new custom" flow
    if (radio === "__new__") {
      setDetailLevel("__new__");
      setCustomLevelName("");
      // Clear all checkboxes for a fresh start
      setDetailDbAccess({ db_safety: false, db_tango: false, db_doculog: false });
      setDetailDbTables({ db_safety: ["*"], db_tango: ["*"], db_doculog: ["*"] });
      const skills: Record<string, boolean> = {};
      for (const k of SKILL_KEYS) skills[k] = false;
      setDetailSkills(skills);
      setDetailSchemaExposure(false);
      return;
    }

    // Built-in level selected
    const defaults = LEVEL_DEFAULTS[radio];
    if (defaults) {
      setDetailLevel(radio);
      setCustomLevelName("");
      setDetailDbAccess({
        db_safety: defaults.db_safety,
        db_tango: defaults.db_tango,
        db_doculog: defaults.db_doculog,
      });
      setDetailDbTables({ db_safety: ["*"], db_tango: ["*"], db_doculog: ["*"] });
      const skills: Record<string, boolean> = {};
      for (const k of SKILL_KEYS) skills[k] = defaults.skills.includes(k);
      setDetailSkills(skills);
      setDetailSchemaExposure(defaults.can_see_schema);
      return;
    }

    // Existing custom template selected — load its policy into the form
    const ct = customTemplates.find((t) => t.name === radio);
    if (ct) {
      setDetailLevel(ct.name);
      setCustomLevelName(ct.name);
      initFormFromTemplatePolicy(ct.policy);
      return;
    }
  }

  async function handleQuickTemplate(userId: number, level: string) {
    clearMessages();

    // "__new_custom__" opens detail panel in new-custom mode
    if (level === "__new_custom__") {
      setSelectedUserId(userId);
      const policy = policies.find((p) => p.user_id === userId);
      if (policy) initFormFromPolicy(policy);
      handleLevelChange("__new__");
      return;
    }

    // Existing custom template — apply it directly
    const ct = customTemplates.find((t) => t.name === level);
    if (ct) {
      const confirmed = window.confirm(`커스텀 정책 "${ct.name}"을(를) 적용하시겠습니까?`);
      if (!confirmed) return;
      try {
        await applySecurityTemplate(userId, ct.name as SecurityLevel);
        setSuccess(`"${ct.name}" 정책이 적용되었습니다.`);
        setFormDirty(false);
        fetchData();
      } catch (err) {
        setError(err instanceof Error ? err.message : "템플릿 적용 실패");
      }
      return;
    }

    // Built-in template
    const label = LEVEL_LABEL[level] ?? level;
    const confirmed = window.confirm(`보안 등급을 ${label}(으)로 변경하시겠습니까?`);
    if (!confirmed) return;
    try {
      await applySecurityTemplate(userId, level as SecurityLevel);
      setSuccess("보안 템플릿이 적용되었습니다.");
      setFormDirty(false);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "템플릿 적용 실패");
    }
  }

  /* ── Build policy data from current form state ── */
  function buildPolicyFromForm(): Record<string, unknown> {
    const enabledSkills = SKILL_KEYS.filter((k) => detailSkills[k]);
    const allSkillsOn = enabledSkills.length === SKILL_KEYS.length;
    return {
      db_access: {
        safety: {
          allowed: detailDbAccess.db_safety ?? false,
          tables: detailDbAccess.db_safety ? (detailDbTables.db_safety ?? ["*"]) : [],
        },
        tango: {
          allowed: detailDbAccess.db_tango ?? false,
          tables: detailDbAccess.db_tango ? (detailDbTables.db_tango ?? ["*"]) : [],
        },
        doculog: {
          allowed: detailDbAccess.db_doculog ?? false,
          tables: detailDbAccess.db_doculog ? (detailDbTables.db_doculog ?? ["*"]) : [],
        },
        platform: { allowed: false, tables: [] },
      },
      allowed_skills: allSkillsOn ? ["*"] : enabledSkills,
      can_see_schema: detailSchemaExposure,
      restricted_topics: [],
    };
  }

  async function handleSaveDetail() {
    if (selectedUserId === null) return;
    clearMessages();
    setSaving(true);
    try {
      const policy = buildPolicyFromForm();

      if (detailLevel === "__new__" && customLevelName.trim()) {
        // Create a new custom template, then apply it to the user
        await createCustomTemplate({
          name: customLevelName.trim(),
          description: "",
          policy: { ...policy, security_level: customLevelName.trim() },
        });
        await applySecurityTemplate(selectedUserId, customLevelName.trim() as SecurityLevel);
        setSuccess(`커스텀 정책 "${customLevelName.trim()}"이(가) 생성 및 적용되었습니다.`);
      } else if (KNOWN_LEVELS.includes(detailLevel as typeof KNOWN_LEVELS[number])) {
        // Built-in level selected — save the actual form values (not the template)
        // This allows admin to select "Standard" but uncheck specific DBs
        await updateSecurityPolicy(selectedUserId, { ...policy, security_level: detailLevel });
        setSuccess("보안 정책이 저장되었습니다.");
      } else {
        // Existing custom template or direct policy update
        // If the checkbox to update the template is on, update the template definition too
        if (updateTemplateOnSave) {
          const ct = customTemplates.find((t) => t.name === detailLevel);
          if (ct) {
            await updateCustomTemplate(ct.id, {
              name: ct.name,
              description: ct.description,
              policy: { ...policy, security_level: detailLevel },
            });
          }
        }
        await updateSecurityPolicy(selectedUserId, { ...policy, security_level: detailLevel });
        setSuccess("보안 정책이 저장되었습니다.");
      }

      setFormDirty(false);
      setUpdateTemplateOnSave(false);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  }

  function handleResetDetail() {
    if (selectedUserId === null) return;
    const p = policies.find((u) => u.user_id === selectedUserId);
    if (!p) return;
    initFormFromPolicy(p);
    setFormDirty(false);
  }

  /* ── Table toggle helper ── */
  function toggleTable(dbKey: string, tableName: string, checked: boolean) {
    setDetailDbTables((prev) => {
      const current = prev[dbKey] ?? [];
      if (checked) {
        return { ...prev, [dbKey]: [...current, tableName] };
      }
      return { ...prev, [dbKey]: current.filter((t) => t !== tableName) };
    });
    setFormDirty(true);
  }

  /* ── Resolve which table list to use for a DB key ── */
  function getTablesForDb(dbKey: string): TableInfo[] {
    if (dbKey === "db_safety") return availableTables.safety;
    if (dbKey === "db_tango") return availableTables.tango;
    if (dbKey === "db_doculog") return availableTables.doculog;
    return [];
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
              <Link href="/security" className="text-blue-600 border-b-2 border-blue-600 pb-0.5">
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
          <div className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
            {error}
          </div>
        )}
        {success && (
          <div className="mb-6 rounded-md bg-green-50 px-4 py-3 text-sm text-green-700">
            {success}
          </div>
        )}

        {/* Stats */}
        <div className={`mb-8 grid grid-cols-1 gap-4 ${customCount > 0 ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
          <StatsCard label="Basic 사용자" value={basicCount} />
          <StatsCard label="Standard 사용자" value={standardCount} />
          <StatsCard label="Full 사용자" value={fullCount} />
          {customCount > 0 && <StatsCard label="Custom 사용자" value={customCount} />}
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12 text-gray-400">
            데이터를 불러오는 중...
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* User Table */}
            <div className="rounded-lg border border-gray-200 bg-white shadow-sm lg:col-span-2">
              <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                <h2 className="text-sm font-semibold text-gray-900">
                  사용자 보안 정책 ({policies.length})
                </h2>
                <span className="text-xs text-gray-400">30초마다 자동 갱신</span>
              </div>

              {policies.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-gray-400">
                  사용자가 없습니다.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          사용자
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          소속
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          보안 등급
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                          빠른 설정
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200 bg-white">
                      {policies.map((p) => (
                        <tr
                          key={p.user_id}
                          onClick={() => handleSelectUser(p.user_id)}
                          className={`cursor-pointer hover:bg-gray-50 ${
                            selectedUserId === p.user_id ? "bg-blue-50" : ""
                          }`}
                        >
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
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => {
                                const val = e.target.value;
                                if (!val) return;
                                handleQuickTemplate(p.user_id, val);
                                e.target.value = "";
                              }}
                              className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              <option value="">
                                {KNOWN_LEVELS.includes(p.security_level as typeof KNOWN_LEVELS[number])
                                  ? LEVEL_LABEL[p.security_level] ?? p.security_level
                                  : p.security_level}
                              </option>
                              <option value="basic">Basic</option>
                              <option value="standard">Standard</option>
                              <option value="full">Full</option>
                              {customTemplates.length > 0 && (
                                <option disabled>──────────</option>
                              )}
                              {customTemplates.map((ct) => (
                                <option key={ct.id} value={ct.name}>
                                  {ct.name}
                                </option>
                              ))}
                              <option disabled>──────────</option>
                              <option value="__new_custom__">+ 새 정책 만들기</option>
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Detail Panel */}
            <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
              <div className="border-b border-gray-200 px-4 py-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-gray-900">상세 설정</h2>
                  {formDirty && (
                    <span className="text-xs font-medium text-amber-600">변경됨</span>
                  )}
                </div>
              </div>

              {selectedPolicy === null ? (
                <div className="flex items-center justify-center py-12 text-sm text-gray-400">
                  사용자를 선택하세요
                </div>
              ) : (
                <div className="space-y-5 px-4 py-4">
                  {/* Selected user info */}
                  <div>
                    <p className="text-sm font-medium text-gray-900">
                      {selectedPolicy.name ?? selectedPolicy.username}
                    </p>
                    <p className="text-xs text-gray-500">{selectedPolicy.username}</p>
                  </div>

                  {/* Security Level Radio */}
                  <fieldset>
                    <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                      보안 등급
                    </legend>
                    <div className="flex flex-col gap-2">
                      {/* Built-in levels */}
                      {BUILTIN_LEVEL_OPTIONS.map((opt) => (
                        <label
                          key={opt.value}
                          className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${
                            selectedRadio === opt.value
                              ? "border-blue-500 bg-blue-50 text-blue-700"
                              : "border-gray-300 text-gray-600 hover:bg-gray-50"
                          }`}
                        >
                          <input
                            type="radio"
                            name="security_level"
                            value={opt.value}
                            checked={selectedRadio === opt.value}
                            onChange={() => handleLevelChange(opt.value)}
                            className="sr-only"
                          />
                          <div className="flex-1">
                            <span className="font-medium">{opt.label}</span>
                            <p className={opt.descClass}>{opt.desc}</p>
                          </div>
                        </label>
                      ))}

                      {/* Separator + Custom templates */}
                      {customTemplates.length > 0 && (
                        <div className="text-xs text-gray-400 mt-3 mb-1 border-t pt-2">
                          Custom 보안 정책
                        </div>
                      )}

                      {customTemplates.map((ct) => (
                        <label
                          key={ct.id}
                          className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${
                            selectedRadio === ct.name
                              ? "border-purple-500 bg-purple-50 text-purple-700"
                              : "border-gray-300 text-gray-600 hover:bg-gray-50"
                          }`}
                        >
                          <input
                            type="radio"
                            name="security_level"
                            value={ct.name}
                            checked={selectedRadio === ct.name}
                            onChange={() => handleLevelChange(ct.name)}
                            className="sr-only"
                          />
                          <div className="flex-1">
                            <span className="font-medium">{ct.name}</span>
                            <p className="text-xs text-gray-400">
                              {ct.description || "관리자 정의 정책"}
                            </p>
                          </div>
                        </label>
                      ))}

                      {/* New custom template */}
                      <div
                        className={`rounded-md border px-3 py-2 text-sm cursor-pointer ${
                          selectedRadio === "__new__"
                            ? "border-purple-500 bg-purple-50"
                            : "border-gray-300 hover:bg-purple-50"
                        }`}
                        onClick={() => handleLevelChange("__new__")}
                      >
                        <span className="text-purple-600 font-medium text-sm">
                          + 새 Custom 정책 만들기
                        </span>
                      </div>

                      {/* Custom name input when creating new */}
                      {selectedRadio === "__new__" && (
                        <input
                          type="text"
                          placeholder="정책 이름 (예: 경영진전용)"
                          value={customLevelName}
                          onChange={(e) => {
                            setCustomLevelName(e.target.value);
                            setFormDirty(true);
                          }}
                          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500"
                        />
                      )}

                      {/* Update template checkbox — shown when an existing custom template is selected and form is dirty */}
                      {formDirty &&
                        !KNOWN_LEVELS.includes(selectedRadio as typeof KNOWN_LEVELS[number]) &&
                        selectedRadio !== "__new__" &&
                        customTemplates.some((ct) => ct.name === selectedRadio) && (
                          <label className="flex items-center gap-2 mt-1 text-xs text-purple-600 bg-purple-50 rounded px-2 py-1.5 border border-purple-200">
                            <input
                              type="checkbox"
                              checked={updateTemplateOnSave}
                              onChange={(e) => setUpdateTemplateOnSave(e.target.checked)}
                              className="h-3.5 w-3.5 rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                            />
                            이 정책의 템플릿도 함께 수정
                          </label>
                        )}
                    </div>
                  </fieldset>

                  {/* DB Access Toggles + Table Selectors */}
                  <fieldset>
                    <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                      DB 접근 권한
                    </legend>
                    <div className="space-y-2">
                      {DB_KEYS.map((key) => (
                        <div key={key}>
                          <label className="flex items-center gap-2 text-sm text-gray-700">
                            <input
                              type="checkbox"
                              checked={detailDbAccess[key] ?? false}
                              onChange={(e) => {
                                setDetailDbAccess((prev) => ({ ...prev, [key]: e.target.checked }));
                                setFormDirty(true);
                              }}
                              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                            />
                            {DB_LABELS[key]}
                          </label>

                          {/* Table-level selector (shown when DB is ON) */}
                          {detailDbAccess[key] && (
                            <div className="ml-6 mt-2">
                              <div className="flex items-center gap-2 mb-1">
                                <label className="text-xs text-gray-500">테이블 접근:</label>
                                <select
                                  value={
                                    detailDbTables[key]?.[0] === "*" ? "all" : "custom"
                                  }
                                  onChange={(e) => {
                                    if (e.target.value === "all") {
                                      setDetailDbTables((prev) => ({
                                        ...prev,
                                        [key]: ["*"],
                                      }));
                                    } else {
                                      setDetailDbTables((prev) => ({
                                        ...prev,
                                        [key]: [],
                                      }));
                                    }
                                    setFormDirty(true);
                                  }}
                                  className="text-xs border rounded px-1 py-0.5"
                                >
                                  <option value="all">전체 테이블</option>
                                  <option value="custom">선택...</option>
                                </select>
                              </div>
                              {detailDbTables[key]?.[0] !== "*" && (
                                <div className="max-h-48 overflow-y-auto border rounded p-2 space-y-1">
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
                                          t.name
                                            .toLowerCase()
                                            .includes(tableFilter.toLowerCase()) ||
                                          t.description
                                            .toLowerCase()
                                            .includes(tableFilter.toLowerCase())
                                      )
                                      .map((t) => (
                                        <label
                                          key={t.name}
                                          className="flex items-center gap-2 text-xs hover:bg-gray-50 px-1 py-0.5 rounded"
                                        >
                                          <input
                                            type="checkbox"
                                            checked={
                                              detailDbTables[key]?.includes(t.name) ?? false
                                            }
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
                                              — {t.description}
                                            </span>
                                          )}
                                        </label>
                                      ))
                                  )}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
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

                  {/* Skill Checkboxes */}
                  <fieldset>
                    <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                      허용 스킬
                    </legend>
                    <div className="grid grid-cols-2 gap-2">
                      {SKILL_KEYS.map((key) => (
                        <label key={key} className="flex items-center gap-2 text-sm text-gray-700">
                          <input
                            type="checkbox"
                            checked={detailSkills[key] ?? false}
                            onChange={(e) => {
                              setDetailSkills((prev) => ({ ...prev, [key]: e.target.checked }));
                              setFormDirty(true);
                            }}
                            className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                          />
                          {SKILL_LABELS[key]}
                        </label>
                      ))}
                    </div>
                  </fieldset>

                  {/* Schema Exposure Toggle */}
                  <fieldset>
                    <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                      스키마 노출
                    </legend>
                    <label className="flex items-center gap-2 text-sm text-gray-700">
                      <input
                        type="checkbox"
                        checked={detailSchemaExposure}
                        onChange={(e) => {
                          setDetailSchemaExposure(e.target.checked);
                          setFormDirty(true);
                        }}
                        className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      DB 스키마 정보 제공
                    </label>
                  </fieldset>

                  {/* Action Buttons */}
                  <div className="flex gap-2 pt-2">
                    <button
                      onClick={handleSaveDetail}
                      disabled={saving}
                      className="flex-1 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
                    >
                      {saving ? "저장 중..." : "저장"}
                    </button>
                    <button
                      onClick={handleResetDetail}
                      className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
                    >
                      초기화
                    </button>
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
