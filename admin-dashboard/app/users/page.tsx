"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getUsers,
  getPendingUsers,
  approveUser,
  updateUserTtl,
  updateUserDeployApps,
  revokeUser,
  rejectUser,
  searchMembers,
  addMemberDirectly,
  updateUserPhone,
  type User,
  type OGuardProfile,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import Pagination, { SearchInput } from "@/components/pagination";

const REFRESH_INTERVAL = 10_000;

const TTL_OPTIONS: { value: string; label: string }[] = [
  { value: "unlimited", label: "만료없음" },
  { value: "weekday-office", label: "평일 09-18시" },
  { value: "30d", label: "30일" },
  { value: "7d", label: "7일" },
  { value: "1d", label: "1일" },
  { value: "8h", label: "8시간" },
  { value: "4h", label: "4시간" },
];

const TTL_LABEL_MAP: Record<string, string> = Object.fromEntries(TTL_OPTIONS.map((o) => [o.value, o.label]));

const TTL_LABEL: Record<string, string> = {
  unlimited: "만료없음",
  "30d": "30일",
  "7d": "7일",
  "1d": "1일",
  "8h": "8시간",
  "4h": "4시간",
};

function roleBadge(role: string) {
  const base =
    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";
  switch (role) {
    case "admin":
      return (
        <span className={`${base} bg-[var(--info-light)] text-[var(--info)]`}>{role}</span>
      );
    case "user":
      return (
        <span className={`${base} bg-[var(--primary-light)] text-[var(--primary)]`}>{role}</span>
      );
    default:
      return (
        <span className={`${base} bg-[var(--surface-hover)] text-[var(--text-muted)]`}>{role}</span>
      );
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function UsersPage() {
  const router = useRouter();
  const [pendingUsers, setPendingUsers] = useState<User[]>([]);
  const [approvedUsers, setApprovedUsers] = useState<User[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<OGuardProfile[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [addTtl, setAddTtl] = useState("4h");
  const [addPhones, setAddPhones] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // TTL for the approval dropdown per pending user (default: "4h")
  const [pendingTtl, setPendingTtl] = useState<Record<number, string>>({});
  const [approvedSearch, setApprovedSearch] = useState("");
  const [approvedPage, setApprovedPage] = useState(1);
  const [pendingSearch, setPendingSearch] = useState("");
  const [pendingPage, setPendingPage] = useState(1);
  const PAGE_SIZE = 10;

  const fetchData = useCallback(async () => {
    try {
      const [pendingRes, allRes] = await Promise.all([
        getPendingUsers(),
        getUsers(),
      ]);
      setPendingUsers(pendingRes.users);
      setApprovedUsers(allRes.users.filter((u) => u.is_approved));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 조회 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearchLoading(true);
    try {
      const res = await searchMembers(searchQuery.trim());
      setSearchResults(res.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : "검색 실패");
    } finally {
      setSearchLoading(false);
    }
  };

  const handleAddMember = async (username: string) => {
    try {
      const phone = addPhones[username]?.trim() || undefined;
      await addMemberDirectly(username, addTtl, phone);
      setSuccess(`${username} 허용목록에 추가 완료${phone ? ` (전화: ${phone})` : ""}`);
      setSearchResults((prev) => prev.filter((r) => r.username !== username));
      setAddPhones((prev) => { const next = { ...prev }; delete next[username]; return next; });
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "추가 실패");
    }
  };

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
      return;
    }
    fetchData();
    const timer = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  function clearMessages() {
    setError("");
    setSuccess("");
  }

  async function handleApprove(userId: number) {
    clearMessages();
    const ttl = pendingTtl[userId] ?? "4h";
    try {
      await approveUser(userId, ttl);
      setSuccess("사용자가 승인되었습니다.");
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "승인 실패");
    }
  }

  async function handleTtlChange(userId: number, newTtl: string) {
    clearMessages();
    try {
      await updateUserTtl(userId, newTtl);
      setSuccess("TTL이 변경되었습니다.");
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "TTL 변경 실패");
    }
  }

  async function handleRevoke(userId: number, username: string) {
    clearMessages();
    const confirmed = window.confirm(
      `${username} 사용자의 승인을 취소하시겠습니까?`
    );
    if (!confirmed) return;

    try {
      await revokeUser(userId);
      setSuccess("승인이 취소되었습니다.");
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "승인 취소 실패");
    }
  }

  const filteredPending = pendingUsers.filter((u) => {
    if (!pendingSearch) return true;
    const q = pendingSearch.toLowerCase();
    return (u.name ?? u.username).toLowerCase().includes(q) ||
      u.username.toLowerCase().includes(q) ||
      (u.region_name || "").toLowerCase().includes(q) ||
      (u.team_name || "").toLowerCase().includes(q);
  });

  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => { setPendingPage(1); }, [pendingSearch]);

  const pendingTotalPages = Math.max(1, Math.ceil(filteredPending.length / PAGE_SIZE));
  const pendingSafePage = Math.min(pendingPage, pendingTotalPages);
  const paginatedPending = filteredPending.slice((pendingSafePage - 1) * PAGE_SIZE, pendingSafePage * PAGE_SIZE);

  const filteredApproved = approvedUsers.filter((u) => {
    if (!approvedSearch) return true;
    const q = approvedSearch.toLowerCase();
    return (u.name ?? u.username).toLowerCase().includes(q) ||
      u.username.toLowerCase().includes(q) ||
      (u.region_name || "").toLowerCase().includes(q) ||
      (u.team_name || "").toLowerCase().includes(q);
  });

  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => { setApprovedPage(1); }, [approvedSearch]);

  const approvedTotalPages = Math.max(1, Math.ceil(filteredApproved.length / PAGE_SIZE));
  const approvedSafePage = Math.min(approvedPage, approvedTotalPages);
  const paginatedApproved = filteredApproved.slice((approvedSafePage - 1) * PAGE_SIZE, approvedSafePage * PAGE_SIZE);

  return (
    <>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {/* Messages */}
        {error && (
          <div className="mb-6 rounded-md bg-[var(--danger-light)] px-4 py-3 text-sm text-[var(--danger)]">
            {error}
          </div>
        )}
        {success && (
          <div className="mb-6 rounded-md bg-[var(--success-light)] px-4 py-3 text-sm text-[var(--success)]">
            {success}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
            데이터를 불러오는 중...
          </div>
        ) : (
          <div className="space-y-8">
            {/* Member Search + Direct Add */}
            <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
              <div className="border-b border-[var(--border)] px-4 py-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">구성원 검색 + 직접 추가</h2>
              </div>
              <div className="px-4 py-3">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                    placeholder="사번 또는 이름으로 검색"
                    className="flex-1 rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm shadow-sm focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                  <select
                    value={addTtl}
                    onChange={(e) => setAddTtl(e.target.value)}
                    className="rounded-md border border-[var(--border-strong)] px-2 py-1.5 text-sm"
                  >
                    {TTL_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                  <button
                    onClick={handleSearch}
                    disabled={searchLoading || !searchQuery.trim()}
                    className="rounded-md bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-white hover:bg-[var(--primary-hover)] disabled:opacity-50"
                  >
                    {searchLoading ? "검색중..." : "검색"}
                  </button>
                </div>
                {searchResults.length > 0 && (
                  <div className="mt-3 overflow-x-auto">
                    <table className="min-w-full divide-y divide-[var(--border)]">
                      <thead className="bg-[var(--bg)]">
                        <tr>
                          <th className="px-3 py-2 text-left text-xs font-medium uppercase text-[var(--text-muted)]">사번</th>
                          <th className="px-3 py-2 text-left text-xs font-medium uppercase text-[var(--text-muted)]">이름</th>
                          <th className="px-3 py-2 text-left text-xs font-medium uppercase text-[var(--text-muted)]">소속</th>
                          <th className="px-3 py-2 text-left text-xs font-medium uppercase text-[var(--text-muted)]">직책</th>
                          <th className="px-3 py-2 text-left text-xs font-medium uppercase text-[var(--text-muted)]">전화번호</th>
                          <th className="px-3 py-2 text-right text-xs font-medium uppercase text-[var(--text-muted)]">추가</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border)]">
                        {searchResults.map((r) => {
                          const alreadyAdded = approvedUsers.some((u) => u.username === r.username);
                          return (
                            <tr key={r.username} className="hover:bg-[var(--bg)]">
                              <td className="px-3 py-2 text-sm text-[var(--text-primary)]">{r.username}</td>
                              <td className="px-3 py-2 text-sm text-[var(--text-primary)]">{r.first_name ?? "-"}</td>
                              <td className="px-3 py-2 text-sm text-[var(--text-secondary)]">{r.region_name ?? "-"} / {r.team_name ?? "-"}</td>
                              <td className="px-3 py-2 text-sm text-[var(--text-secondary)]">{r.job_name ?? "-"}</td>
                              <td className="px-3 py-2">
                                {!alreadyAdded && (
                                  <input
                                    type="tel"
                                    placeholder="010-0000-0000"
                                    value={addPhones[r.username] || ""}
                                    onChange={(e) => setAddPhones((prev) => ({ ...prev, [r.username]: e.target.value }))}
                                    className="w-32 rounded border border-[var(--border-strong)] px-2 py-0.5 text-xs"
                                  />
                                )}
                              </td>
                              <td className="px-3 py-2 text-right">
                                {alreadyAdded ? (
                                  <span className="text-xs text-[var(--text-muted)]">등록됨</span>
                                ) : (
                                  <button
                                    onClick={() => handleAddMember(r.username)}
                                    className="rounded bg-[var(--success)] px-3 py-1 text-xs font-medium text-white hover:bg-[var(--success)]"
                                  >
                                    추가
                                  </button>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            {/* Pending Users */}
            <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
              <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                  승인 대기 ({pendingUsers.length})
                </h2>
                <div className="flex items-center gap-3">
                  <SearchInput value={pendingSearch} onChange={setPendingSearch} placeholder="대기자 검색..." />
                  {pendingSearch && <span className="text-xs text-[var(--text-muted)]">{filteredPending.length}건</span>}
                  <span className="text-xs text-[var(--text-muted)]">10초마다 자동 갱신</span>
                </div>
              </div>

              {filteredPending.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
                  {pendingSearch ? "검색 결과가 없습니다." : "승인 대기 중인 사용자가 없습니다."}
                </div>
              ) : (
                <>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-[var(--border)]">
                    <thead className="bg-[var(--bg)]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          이름 (사번)
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          소속
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          역할
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          Pod TTL
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          최근 로그인
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          작업
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                      {paginatedPending.map((u) => (
                        <tr key={u.id} className="hover:bg-[var(--bg)]">
                          <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                            {u.name ?? u.username}
                            <span className="ml-1 text-xs text-[var(--text-muted)]">({u.username})</span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                            {[u.region_name, u.team_name, u.job_name].filter(Boolean).join(" / ") || "-"}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            {roleBadge(u.role)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <select
                              value={pendingTtl[u.id] ?? "4h"}
                              onChange={(e) =>
                                setPendingTtl((prev) => ({
                                  ...prev,
                                  [u.id]: e.target.value,
                                }))
                              }
                              className="rounded-md border border-[var(--border-strong)] px-2 py-1 text-sm shadow-sm focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                            >
                              {TTL_OPTIONS.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                            {formatDate(u.last_login_at)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <div className="flex gap-1.5">
                              <button
                                onClick={() => handleApprove(u.id)}
                                className="rounded-md bg-[var(--primary)] px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-[var(--primary-hover)] transition-colors"
                              >
                                승인
                              </button>
                              <button
                                onClick={() => {
                                  if (confirm(`${u.name ?? u.username} 사용자를 거절하시겠습니까? 목록에서 삭제됩니다.`)) {
                                    rejectUser(u.id).then(() => fetchData());
                                  }
                                }}
                                className="rounded-md border border-[var(--danger)] px-3 py-1.5 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] transition-colors"
                              >
                                거절
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  currentPage={pendingSafePage}
                  totalPages={pendingTotalPages}
                  totalItems={filteredPending.length}
                  itemsPerPage={PAGE_SIZE}
                  onPageChange={setPendingPage}
                />
                </>
              )}
            </div>

            {/* Approved Users */}
            <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
              <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                  허용 목록 ({approvedUsers.length})
                </h2>
                <div className="flex items-center gap-2">
                  <SearchInput value={approvedSearch} onChange={setApprovedSearch} placeholder="사용자 검색..." />
                  {approvedSearch && <span className="text-xs text-[var(--text-muted)]">{filteredApproved.length}건</span>}
                </div>
              </div>

              {filteredApproved.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
                  {approvedSearch ? "검색 결과가 없습니다." : "승인된 사용자가 없습니다."}
                </div>
              ) : (
                <>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-[var(--border)]">
                    <thead className="bg-[var(--bg)]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          이름 (사번)
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          소속
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          역할
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          전화번호
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          Pod TTL
                        </th>
                        <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          앱 배포
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          승인일
                        </th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          작업
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border)] bg-[var(--surface)]">
                      {paginatedApproved.map((u) => (
                        <tr key={u.id} className="hover:bg-[var(--bg)]">
                          <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
                            {u.name ?? u.username}
                            <span className="ml-1 text-xs text-[var(--text-muted)]">({u.username})</span>
                            {u.is_presenter && (
                              <span className="ml-1.5 inline-flex items-center rounded-full bg-[var(--info-light)] px-2 py-0.5 text-xs font-medium text-[var(--info)]">
                                전용
                              </span>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                            {[u.region_name, u.team_name, u.job_name].filter(Boolean).join(" / ") || "-"}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            {roleBadge(u.role)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <input
                              type="tel"
                              defaultValue={u.phone_number || ""}
                              placeholder="010-0000-0000"
                              onBlur={async (e) => {
                                const val = e.target.value.trim();
                                if (val !== (u.phone_number || "")) {
                                  try {
                                    await updateUserPhone(u.id, val);
                                    setSuccess(`${u.name || u.username} 전화번호 저장됨`);
                                    fetchData();
                                  } catch { setError("전화번호 저장 실패"); }
                                }
                              }}
                              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                              className="w-32 rounded border border-[var(--border-strong)] px-2 py-0.5 text-xs focus:border-[var(--primary)] focus:outline-none"
                            />
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <select
                              value={u.pod_ttl}
                              onChange={(e) =>
                                handleTtlChange(u.id, e.target.value)
                              }
                              className="rounded-md border border-[var(--border-strong)] px-2 py-1 text-sm shadow-sm focus:border-[var(--primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                            >
                              {TTL_OPTIONS.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-center">
                            <button
                              onClick={async () => {
                                try {
                                  await updateUserDeployApps(u.id, !u.can_deploy_apps);
                                  setSuccess(`${u.name || u.username} 앱 배포 권한 ${!u.can_deploy_apps ? "부여" : "회수"}`);
                                  fetchData();
                                } catch { setError("앱 배포 권한 변경 실패"); }
                              }}
                              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${u.can_deploy_apps ? "bg-[var(--primary)]" : "bg-gray-300"}`}
                            >
                              <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-[var(--surface)] transition-transform ${u.can_deploy_apps ? "translate-x-4" : "translate-x-0.5"}`} />
                            </button>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm text-[var(--text-secondary)]">
                            {formatDate(u.approved_at)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-sm">
                            <button
                              onClick={() => handleRevoke(u.id, u.username)}
                              className="rounded bg-[var(--danger-light)] px-3 py-1.5 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] transition-colors"
                            >
                              승인취소
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  currentPage={approvedSafePage}
                  totalPages={approvedTotalPages}
                  totalItems={filteredApproved.length}
                  itemsPerPage={PAGE_SIZE}
                  onPageChange={setApprovedPage}
                />
                </>
              )}
            </div>
          </div>
        )}
      </main>
    </>
  );
}
