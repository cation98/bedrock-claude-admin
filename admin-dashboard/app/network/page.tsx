"use client";

import { useEffect, useState } from "react";
import {
  getAllowedDomains,
  addAllowedDomain,
  updateAllowedDomain,
  deleteAllowedDomain,
  getProxyLogs,
  type AllowedDomain,
  type ProxyAccessLog,
} from "@/lib/api";

type Tab = "domains" | "logs";

export default function NetworkPage() {
  const [tab, setTab] = useState<Tab>("domains");

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">네트워크 관리</h1>

      {/* Tab Buttons */}
      <div className="mb-4 flex gap-2 border-b border-[var(--border)]">
        <button
          className={`px-4 py-2 text-sm font-medium ${
            tab === "domains"
              ? "border-b-2 border-[var(--primary)] text-[var(--primary)]"
              : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
          }`}
          onClick={() => setTab("domains")}
        >
          허용 도메인
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium ${
            tab === "logs"
              ? "border-b-2 border-[var(--primary)] text-[var(--primary)]"
              : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
          }`}
          onClick={() => setTab("logs")}
        >
          프록시 로그
        </button>
      </div>

      {tab === "domains" && <DomainsPanel />}
      {tab === "logs" && <LogsPanel />}
    </div>
  );
}

/* ==================== Domains Panel ==================== */

function DomainsPanel() {
  const [domains, setDomains] = useState<AllowedDomain[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Add form state
  const [newDomain, setNewDomain] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newWildcard, setNewWildcard] = useState(false);
  const [adding, setAdding] = useState(false);

  const fetchDomains = async () => {
    try {
      setLoading(true);
      const res = await getAllowedDomains();
      setDomains(res.domains);
      setError("");
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDomains();
  }, []);

  const handleAdd = async () => {
    if (!newDomain.trim()) return;
    try {
      setAdding(true);
      await addAllowedDomain({
        domain: newDomain.trim(),
        description: newDesc.trim() || undefined,
        is_wildcard: newWildcard,
      });
      setNewDomain("");
      setNewDesc("");
      setNewWildcard(false);
      await fetchDomains();
    } catch (e) {
      setError(String(e));
    } finally {
      setAdding(false);
    }
  };

  const handleToggle = async (d: AllowedDomain) => {
    try {
      await updateAllowedDomain(d.id, { enabled: !d.enabled });
      await fetchDomains();
    } catch (e) {
      setError(String(e));
    }
  };

  const handleDelete = async (d: AllowedDomain) => {
    if (!confirm(`'${d.domain}' 도메인을 삭제하시겠습니까?`)) return;
    try {
      await deleteAllowedDomain(d.id);
      await fetchDomains();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div>
      {error && (
        <div className="mb-4 rounded bg-[var(--danger-light)] px-4 py-2 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {/* Add Form */}
      <div className="mb-4 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
        <h3 className="mb-3 text-sm font-semibold text-[var(--text-secondary)]">도메인 추가</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[200px]">
            <label className="mb-1 block text-xs text-[var(--text-muted)]">도메인</label>
            <input
              type="text"
              className="w-full rounded border border-[var(--border-strong)] px-3 py-1.5 text-sm"
              placeholder="apis.data.go.kr 또는 *.amazonaws.com"
              value={newDomain}
              onChange={(e) => setNewDomain(e.target.value)}
            />
          </div>
          <div className="flex-1 min-w-[200px]">
            <label className="mb-1 block text-xs text-[var(--text-muted)]">설명</label>
            <input
              type="text"
              className="w-full rounded border border-[var(--border-strong)] px-3 py-1.5 text-sm"
              placeholder="공공데이터 포탈 API"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-1.5 text-sm text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={newWildcard}
              onChange={(e) => setNewWildcard(e.target.checked)}
            />
            와일드카드
          </label>
          <button
            onClick={handleAdd}
            disabled={adding || !newDomain.trim()}
            className="rounded bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-white hover:bg-[var(--primary-hover)] disabled:opacity-50"
          >
            {adding ? "추가 중..." : "추가"}
          </button>
        </div>
      </div>

      {/* Domains Table */}
      <div className="overflow-x-auto rounded-lg border border-[var(--border)] bg-[var(--surface)]">
        <table className="w-full text-left text-sm">
          <thead className="bg-[var(--bg)] text-xs uppercase text-[var(--text-muted)]">
            <tr>
              <th className="px-4 py-3">도메인</th>
              <th className="px-4 py-3">와일드카드</th>
              <th className="px-4 py-3">설명</th>
              <th className="px-4 py-3">상태</th>
              <th className="px-4 py-3">등록자</th>
              <th className="px-4 py-3">액션</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--text-muted)]">
                  로딩 중...
                </td>
              </tr>
            ) : domains.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--text-muted)]">
                  등록된 도메인이 없습니다
                </td>
              </tr>
            ) : (
              domains.map((d) => (
                <tr key={d.id} className="border-t border-[var(--border)] hover:bg-[var(--bg)]">
                  <td className="px-4 py-2 font-mono text-xs">{d.domain}</td>
                  <td className="px-4 py-2">
                    {d.is_wildcard ? (
                      <span className="rounded bg-[var(--info-light)] px-2 py-0.5 text-xs text-[var(--info)]">
                        Y
                      </span>
                    ) : (
                      <span className="text-xs text-[var(--text-muted)]">-</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-[var(--text-secondary)]">
                    {d.description || "-"}
                  </td>
                  <td className="px-4 py-2">
                    <button
                      onClick={() => handleToggle(d)}
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        d.enabled
                          ? "bg-[var(--success-light)] text-[var(--success)]"
                          : "bg-[var(--surface-hover)] text-[var(--text-muted)]"
                      }`}
                    >
                      {d.enabled ? "활성" : "비활성"}
                    </button>
                  </td>
                  <td className="px-4 py-2 text-xs text-[var(--text-muted)]">
                    {d.created_by || "-"}
                  </td>
                  <td className="px-4 py-2">
                    <button
                      onClick={() => handleDelete(d)}
                      className="text-xs text-[var(--danger)] hover:text-[var(--danger)]"
                    >
                      삭제
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ==================== Logs Panel ==================== */

function LogsPanel() {
  const [logs, setLogs] = useState<ProxyAccessLog[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [filterUser, setFilterUser] = useState("");
  const [filterDomain, setFilterDomain] = useState("");
  const limit = 50;

  const fetchLogs = async () => {
    try {
      setLoading(true);
      const res = await getProxyLogs({
        skip: page * limit,
        limit,
        user_id: filterUser || undefined,
        domain: filterDomain || undefined,
      });
      setLogs(res.logs);
      setTotal(res.total);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, [page, filterUser, filterDomain]);

  const totalPages = Math.ceil(total / limit);

  return (
    <div>
      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <div>
          <label className="mb-1 block text-xs text-[var(--text-muted)]">사용자 ID</label>
          <input
            type="text"
            className="rounded border border-[var(--border-strong)] px-3 py-1.5 text-sm"
            placeholder="N1102359"
            value={filterUser}
            onChange={(e) => {
              setFilterUser(e.target.value);
              setPage(0);
            }}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-[var(--text-muted)]">도메인</label>
          <input
            type="text"
            className="rounded border border-[var(--border-strong)] px-3 py-1.5 text-sm"
            placeholder="amazonaws.com"
            value={filterDomain}
            onChange={(e) => {
              setFilterDomain(e.target.value);
              setPage(0);
            }}
          />
        </div>
      </div>

      {/* Logs Table */}
      <div className="overflow-x-auto rounded-lg border border-[var(--border)] bg-[var(--surface)]">
        <table className="w-full text-left text-sm">
          <thead className="bg-[var(--bg)] text-xs uppercase text-[var(--text-muted)]">
            <tr>
              <th className="px-4 py-3">시각</th>
              <th className="px-4 py-3">사용자</th>
              <th className="px-4 py-3">도메인</th>
              <th className="px-4 py-3">메서드</th>
              <th className="px-4 py-3">결과</th>
              <th className="px-4 py-3">응답시간</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--text-muted)]">
                  로딩 중...
                </td>
              </tr>
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--text-muted)]">
                  로그가 없습니다
                </td>
              </tr>
            ) : (
              logs.map((log) => (
                <tr
                  key={log.id}
                  className="border-t border-[var(--border)] hover:bg-[var(--bg)]"
                >
                  <td className="px-4 py-2 text-xs text-[var(--text-muted)]">
                    {log.created_at
                      ? new Date(log.created_at).toLocaleString("ko-KR")
                      : "-"}
                  </td>
                  <td className="px-4 py-2 text-xs font-mono">
                    {log.user_id || "-"}
                  </td>
                  <td className="px-4 py-2 text-xs font-mono">
                    {log.domain || "-"}
                  </td>
                  <td className="px-4 py-2 text-xs">{log.method || "-"}</td>
                  <td className="px-4 py-2">
                    {log.allowed ? (
                      <span className="rounded bg-[var(--success-light)] px-2 py-0.5 text-xs text-[var(--success)]">
                        허용
                      </span>
                    ) : (
                      <span className="rounded bg-[var(--danger-light)] px-2 py-0.5 text-xs text-[var(--danger)]">
                        차단
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-[var(--text-muted)]">
                    {log.response_time_ms !== null
                      ? `${log.response_time_ms}ms`
                      : "-"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-between text-sm text-[var(--text-muted)]">
          <span>
            총 {total}건 (페이지 {page + 1}/{totalPages})
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="rounded border border-[var(--border-strong)] px-3 py-1 text-xs hover:bg-[var(--bg)] disabled:opacity-50"
            >
              이전
            </button>
            <button
              onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="rounded border border-[var(--border-strong)] px-3 py-1 text-xs hover:bg-[var(--bg)] disabled:opacity-50"
            >
              다음
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
