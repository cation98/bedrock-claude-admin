"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  getSecurityPolicies,
  getSecurityTables,
  applySecurityTemplate,
  updateSecurityPolicy,
  type SecurityPolicyWithUser,
  type SecurityLevel,
  type TableInfo,
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

const LEVEL_OPTIONS: {
  value: string;
  label: string;
  desc: string;
  descClass: string;
}[] = [
  { value: "basic", label: "Basic", desc: "스킬만 사용, DB 직접접근 불가", descClass: "text-xs text-gray-400" },
  { value: "standard", label: "Standard", desc: "허용 DB/테이블 접근, 전체 스킬", descClass: "text-xs text-gray-400" },
  { value: "full", label: "Full", desc: "전체 접근 (주의: 모든 데이터 열람 가능)", descClass: "text-xs text-amber-500" },
  { value: "custom", label: "Custom", desc: "관리자 정의 권한 (직접 설정)", descClass: "text-xs text-purple-500" },
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

function parsePolicyField<T>(policy: Record<string, unknown>, key: string, fallback: T): T {
  return (policy[key] as T) ?? fallback;
}

/** Determine the radio selection for a given security_level string */
function resolveRadioSelection(level: string): string {
  if (KNOWN_LEVELS.includes(level as typeof KNOWN_LEVELS[number])) return level;
  return "custom";
}

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

  // Table filter for search
  const [tableFilter, setTableFilter] = useState("");

  // Ref to track formDirty in the interval callback without re-creating it
  const formDirtyRef = useRef(formDirty);
  formDirtyRef.current = formDirty;

  const selectedUserIdRef = useRef(selectedUserId);
  selectedUserIdRef.current = selectedUserId;

  /* ── Populate form fields from a policy object ── */
  const initFormFromPolicy = useCallback((p: SecurityPolicyWithUser) => {
    const radio = resolveRadioSelection(p.security_level);
    setSelectedRadio(radio);
    if (radio === "custom") {
      setDetailLevel(p.security_level);
      setCustomLevelName(p.security_level);
    } else {
      setDetailLevel(p.security_level);
      setCustomLevelName("");
    }
    setDetailDbAccess({
      db_safety: parsePolicyField(p.security_policy, "db_safety", false),
      db_tango: parsePolicyField(p.security_policy, "db_tango", false),
      db_doculog: parsePolicyField(p.security_policy, "db_doculog", false),
    });
    setDetailDbTables({
      db_safety: parsePolicyField<string[]>(p.security_policy, "db_safety_tables", ["*"]),
      db_tango: parsePolicyField<string[]>(p.security_policy, "db_tango_tables", ["*"]),
      db_doculog: parsePolicyField<string[]>(p.security_policy, "db_doculog_tables", ["*"]),
    });
    const skills: Record<string, boolean> = {};
    for (const k of SKILL_KEYS) {
      skills[k] = parsePolicyField(p.security_policy, `skill_${k}`, false);
    }
    setDetailSkills(skills);
    setDetailSchemaExposure(parsePolicyField(p.security_policy, "schema_exposure", false));
    setTableFilter("");
  }, []);

  /* ── Data fetch — NEVER touches form state ── */
  const fetchData = useCallback(async () => {
    try {
      const [policyRes, tablesRes] = await Promise.all([
        getSecurityPolicies(),
        getSecurityTables().catch(() => ({ safety: [], tango: [], doculog: [] })),
      ]);
      setPolicies(policyRes.policies);
      setAvailableTables(tablesRes);

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

    if (radio === "custom") {
      // Don't auto-fill — admin sets everything manually
      setDetailLevel(customLevelName || "custom");
      return;
    }

    const defaults = LEVEL_DEFAULTS[radio];
    if (!defaults) return;

    setDetailLevel(radio);
    setCustomLevelName("");
    setDetailDbAccess({
      db_safety: defaults.db_safety,
      db_tango: defaults.db_tango,
      db_doculog: defaults.db_doculog,
    });
    setDetailDbTables({
      db_safety: ["*"],
      db_tango: ["*"],
      db_doculog: ["*"],
    });
    const skills: Record<string, boolean> = {};
    for (const k of SKILL_KEYS) {
      skills[k] = defaults.skills.includes(k);
    }
    setDetailSkills(skills);
    setDetailSchemaExposure(defaults.can_see_schema);
  }

  async function handleQuickTemplate(userId: number, level: string) {
    clearMessages();
    if (level === "custom") {
      // "Custom" in the quick dropdown opens the detail panel instead
      setSelectedUserId(userId);
      const policy = policies.find((p) => p.user_id === userId);
      if (policy) initFormFromPolicy(policy);
      setSelectedRadio("custom");
      setFormDirty(false);
      return;
    }
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

  async function handleSaveDetail() {
    if (selectedUserId === null) return;
    clearMessages();
    setSaving(true);
    try {
      const policyData: Record<string, unknown> = {
        security_level: detailLevel,
        db_safety: detailDbAccess.db_safety ?? false,
        db_tango: detailDbAccess.db_tango ?? false,
        db_doculog: detailDbAccess.db_doculog ?? false,
        db_safety_tables: detailDbTables.db_safety ?? ["*"],
        db_tango_tables: detailDbTables.db_tango ?? ["*"],
        db_doculog_tables: detailDbTables.db_doculog ?? ["*"],
        db_platform: false, // always OFF
        schema_exposure: detailSchemaExposure,
      };
      for (const k of SKILL_KEYS) {
        policyData[`skill_${k}`] = detailSkills[k] ?? false;
      }
      await updateSecurityPolicy(selectedUserId, policyData);
      setSuccess("보안 정책이 저장되었습니다.");
      setFormDirty(false);
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
                              value={
                                KNOWN_LEVELS.includes(p.security_level as typeof KNOWN_LEVELS[number])
                                  ? p.security_level
                                  : "custom"
                              }
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) =>
                                handleQuickTemplate(p.user_id, e.target.value)
                              }
                              className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              <option value="basic">Basic</option>
                              <option value="standard">Standard</option>
                              <option value="full">Full</option>
                              <option value="custom">Custom...</option>
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
                      {LEVEL_OPTIONS.map((opt) => (
                        <label
                          key={opt.value}
                          className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${
                            selectedRadio === opt.value
                              ? opt.value === "custom"
                                ? "border-purple-500 bg-purple-50 text-purple-700"
                                : "border-blue-500 bg-blue-50 text-blue-700"
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
                            {/* Custom name input */}
                            {opt.value === "custom" && selectedRadio === "custom" && (
                              <div className="mt-2">
                                <input
                                  type="text"
                                  placeholder="등급 이름 (예: 경영진전용)"
                                  value={customLevelName}
                                  onChange={(e) => {
                                    setCustomLevelName(e.target.value);
                                    setDetailLevel(e.target.value || "custom");
                                    setFormDirty(true);
                                  }}
                                  onClick={(e) => e.stopPropagation()}
                                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500"
                                />
                              </div>
                            )}
                          </div>
                        </label>
                      ))}
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
