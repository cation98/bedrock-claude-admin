"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  getSecurityPolicies,
  applySecurityTemplate,
  updateSecurityPolicy,
  type SecurityPolicyWithUser,
  type SecurityLevel,
} from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import StatsCard from "@/components/stats-card";

const REFRESH_INTERVAL = 30_000;

const LEVEL_BADGE: Record<SecurityLevel, string> = {
  basic: "bg-gray-100 text-gray-600",
  standard: "bg-blue-100 text-blue-700",
  full: "bg-green-100 text-green-700",
};

const LEVEL_LABEL: Record<SecurityLevel, string> = {
  basic: "Basic",
  standard: "Standard",
  full: "Full",
};

const DB_KEYS = ["db_safety", "db_tango"] as const;
const DB_LABELS: Record<string, string> = {
  db_safety: "Safety DB",
  db_tango: "Tango DB",
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

function parsePolicyField<T>(policy: Record<string, unknown>, key: string, fallback: T): T {
  return (policy[key] as T) ?? fallback;
}

export default function SecurityPage() {
  const router = useRouter();
  const [policies, setPolicies] = useState<SecurityPolicyWithUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

  // Detail panel local state
  const [detailLevel, setDetailLevel] = useState<SecurityLevel>("basic");
  const [detailDbAccess, setDetailDbAccess] = useState<Record<string, boolean>>({});
  const [detailSkills, setDetailSkills] = useState<Record<string, boolean>>({});
  const [detailSchemaExposure, setDetailSchemaExposure] = useState(false);
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await getSecurityPolicies();
      setPolicies(res.policies);
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
    const timer = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  // Sync detail panel when selection changes
  useEffect(() => {
    if (selectedUserId === null) return;
    const p = policies.find((u) => u.user_id === selectedUserId);
    if (!p) return;
    setDetailLevel(p.security_level);
    setDetailDbAccess({
      db_safety: parsePolicyField(p.security_policy, "db_safety", false),
      db_tango: parsePolicyField(p.security_policy, "db_tango", false),
    });
    const skills: Record<string, boolean> = {};
    for (const k of SKILL_KEYS) {
      skills[k] = parsePolicyField(p.security_policy, `skill_${k}`, false);
    }
    setDetailSkills(skills);
    setDetailSchemaExposure(parsePolicyField(p.security_policy, "schema_exposure", false));
  }, [selectedUserId, policies]);

  const user = getUser();
  const selectedPolicy = policies.find((u) => u.user_id === selectedUserId) ?? null;

  const basicCount = policies.filter((p) => p.security_level === "basic").length;
  const standardCount = policies.filter((p) => p.security_level === "standard").length;
  const fullCount = policies.filter((p) => p.security_level === "full").length;

  function clearMessages() {
    setError("");
    setSuccess("");
  }

  async function handleQuickTemplate(userId: number, level: SecurityLevel) {
    clearMessages();
    const confirmed = window.confirm(`보안 등급을 ${LEVEL_LABEL[level]}(으)로 변경하시겠습니까?`);
    if (!confirmed) return;
    try {
      await applySecurityTemplate(userId, level);
      setSuccess("보안 템플릿이 적용되었습니다.");
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
        db_platform: false, // always OFF
        schema_exposure: detailSchemaExposure,
      };
      for (const k of SKILL_KEYS) {
        policyData[`skill_${k}`] = detailSkills[k] ?? false;
      }
      await updateSecurityPolicy(selectedUserId, policyData);
      setSuccess("보안 정책이 저장되었습니다.");
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  }

  function handleResetDetail() {
    if (selectedUserId === null) return;
    // Re-trigger sync from current data
    const p = policies.find((u) => u.user_id === selectedUserId);
    if (!p) return;
    setDetailLevel(p.security_level);
    setDetailDbAccess({
      db_safety: parsePolicyField(p.security_policy, "db_safety", false),
      db_tango: parsePolicyField(p.security_policy, "db_tango", false),
    });
    const skills: Record<string, boolean> = {};
    for (const k of SKILL_KEYS) {
      skills[k] = parsePolicyField(p.security_policy, `skill_${k}`, false);
    }
    setDetailSkills(skills);
    setDetailSchemaExposure(parsePolicyField(p.security_policy, "schema_exposure", false));
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
        <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatsCard label="Basic 사용자" value={basicCount} />
          <StatsCard label="Standard 사용자" value={standardCount} />
          <StatsCard label="Full 사용자" value={fullCount} />
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
                          onClick={() => setSelectedUserId(p.user_id)}
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
                            <span
                              className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                                LEVEL_BADGE[p.security_level]
                              }`}
                            >
                              {LEVEL_LABEL[p.security_level]}
                            </span>
                            {p.pod_restart_required && (
                              <span className="ml-1.5 inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-700">
                                재시작 필요
                              </span>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <select
                              value={p.security_level}
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) =>
                                handleQuickTemplate(p.user_id, e.target.value as SecurityLevel)
                              }
                              className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              <option value="basic">Basic</option>
                              <option value="standard">Standard</option>
                              <option value="full">Full</option>
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
                <h2 className="text-sm font-semibold text-gray-900">상세 설정</h2>
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
                    <div className="flex gap-3">
                      {(["basic", "standard", "full"] as SecurityLevel[]).map((lvl) => (
                        <label
                          key={lvl}
                          className={`flex cursor-pointer items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm ${
                            detailLevel === lvl
                              ? "border-blue-500 bg-blue-50 text-blue-700"
                              : "border-gray-300 text-gray-600 hover:bg-gray-50"
                          }`}
                        >
                          <input
                            type="radio"
                            name="security_level"
                            value={lvl}
                            checked={detailLevel === lvl}
                            onChange={() => setDetailLevel(lvl)}
                            className="sr-only"
                          />
                          {LEVEL_LABEL[lvl]}
                        </label>
                      ))}
                    </div>
                  </fieldset>

                  {/* DB Access Toggles */}
                  <fieldset>
                    <legend className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                      DB 접근 권한
                    </legend>
                    <div className="space-y-2">
                      {DB_KEYS.map((key) => (
                        <label key={key} className="flex items-center gap-2 text-sm text-gray-700">
                          <input
                            type="checkbox"
                            checked={detailDbAccess[key] ?? false}
                            onChange={(e) =>
                              setDetailDbAccess((prev) => ({ ...prev, [key]: e.target.checked }))
                            }
                            className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                          />
                          {DB_LABELS[key]}
                        </label>
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
                            onChange={(e) =>
                              setDetailSkills((prev) => ({ ...prev, [key]: e.target.checked }))
                            }
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
                        onChange={(e) => setDetailSchemaExposure(e.target.checked)}
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
