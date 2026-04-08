"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getGovernanceDashboard,
  getGovernanceFiles,
  type GovernanceDashboardStats,
  type GovernedFileItem,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import StatsCard from "@/components/stats-card";
import Pagination from "@/components/pagination";

const REFRESH_INTERVAL = 30_000;
const PAGE_SIZE = 20;

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function ClassificationBadge({ value }: { value: GovernedFileItem["classification"] }) {
  if (value === "sensitive") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
        민감
      </span>
    );
  }
  if (value === "normal") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
        일반
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-medium text-cyan-700">
      미분류
    </span>
  );
}

function StatusBadge({ value }: { value: GovernedFileItem["status"] }) {
  if (value === "active") {
    return (
      <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
        활성
      </span>
    );
  }
  if (value === "quarantine") {
    return (
      <span className="inline-flex items-center rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-medium text-cyan-700">
        격리
      </span>
    );
  }
  if (value === "expired") {
    return (
      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500">
        만료
      </span>
    );
  }
  // deleted
  return (
    <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-400 line-through">
      삭제
    </span>
  );
}

function TtlCell({ ttl_days }: { ttl_days: number | null }) {
  if (ttl_days === null) return <span className="text-gray-400">—</span>;
  if (ttl_days <= 3) {
    return (
      <span className="font-medium text-amber-600">{ttl_days}일</span>
    );
  }
  return <span className="text-gray-700">{ttl_days}일</span>;
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function DataGovernancePage() {
  const router = useRouter();

  // Stats
  const [stats, setStats] = useState<GovernanceDashboardStats>({
    total_files: 0,
    sensitive_files: 0,
    expiring_soon: 0,
    storage_used_bytes: 0,
  });

  // File list
  const [files, setFiles] = useState<GovernedFileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);

  // Filter state (applied on search button click)
  const [filterClassification, setFilterClassification] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterUsername, setFilterUsername] = useState("");

  // Active filters (sent to API)
  const [activeClassification, setActiveClassification] = useState("");
  const [activeStatus, setActiveStatus] = useState("");
  const [activeUsername, setActiveUsername] = useState("");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async (currentPage: number, classification: string, status: string, username: string) => {
    try {
      const [statsRes, filesRes] = await Promise.all([
        getGovernanceDashboard(),
        getGovernanceFiles({
          classification: classification || undefined,
          status: status || undefined,
          username: username || undefined,
          page: currentPage,
          per_page: PAGE_SIZE,
        }),
      ]);
      setStats(statsRes);
      setFiles(filesRes.files);
      setTotal(filesRes.total);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Auth check
  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/");
    }
  }, [router]);

  // Initial load + auto-refresh
  useEffect(() => {
    fetchData(page, activeClassification, activeStatus, activeUsername);
    const timer = setInterval(
      () => fetchData(page, activeClassification, activeStatus, activeUsername),
      REFRESH_INTERVAL
    );
    return () => clearInterval(timer);
  }, [fetchData, page, activeClassification, activeStatus, activeUsername]);

  const handleSearch = () => {
    setPage(1);
    setActiveClassification(filterClassification);
    setActiveStatus(filterStatus);
    setActiveUsername(filterUsername);
  };

  const handlePageChange = (p: number) => {
    setPage(p);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-gray-900">데이터 거버넌스</h1>
        <p className="mt-1 text-sm text-gray-500">사용자 파일 분류 및 TTL 관리</p>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatsCard label="전체 파일" value={stats.total_files.toLocaleString()} />
        <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <p className="text-sm font-medium text-gray-500">민감 파일</p>
          <p className="mt-2 text-3xl font-semibold text-red-600">{stats.sensitive_files.toLocaleString()}</p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <p className="text-sm font-medium text-gray-500">만료 임박</p>
          <p className="mt-2 text-3xl font-semibold text-amber-600">{stats.expiring_soon.toLocaleString()}</p>
        </div>
        <StatsCard label="스토리지" value={formatBytes(stats.storage_used_bytes)} />
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3">
        <select
          value={filterClassification}
          onChange={(e) => setFilterClassification(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">전체 분류</option>
          <option value="sensitive">민감</option>
          <option value="normal">일반</option>
          <option value="unknown">미분류</option>
        </select>

        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">전체 상태</option>
          <option value="active">활성</option>
          <option value="quarantine">격리</option>
          <option value="expired">만료</option>
        </select>

        <input
          type="text"
          value={filterUsername}
          onChange={(e) => setFilterUsername(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="사용자명 검색"
          className="rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />

        <button
          onClick={handleSearch}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          검색
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* File Table */}
      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">사용자</th>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">파일명</th>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">분류</th>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">TTL</th>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">유형</th>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">크기</th>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">상태</th>
                <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">등록일</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {loading ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-sm text-gray-400">
                    불러오는 중...
                  </td>
                </tr>
              ) : files.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-sm text-gray-400">
                    파일이 없습니다.
                  </td>
                </tr>
              ) : (
                files.map((file) => (
                  <tr key={file.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-2.5 text-gray-700">{file.username}</td>
                    <td className="max-w-xs px-4 py-2.5">
                      <span className="block truncate text-gray-900" title={file.file_path}>
                        {file.filename}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5">
                      <ClassificationBadge value={file.classification} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5">
                      <TtlCell ttl_days={file.ttl_days} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-gray-600">{file.file_type || "—"}</td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-gray-600">{formatBytes(file.file_size_bytes)}</td>
                    <td className="whitespace-nowrap px-4 py-2.5">
                      <StatusBadge value={file.status} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-gray-500">{formatDate(file.created_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <Pagination
          currentPage={page}
          totalPages={totalPages}
          totalItems={total}
          itemsPerPage={PAGE_SIZE}
          onPageChange={handlePageChange}
        />
      </div>
    </div>
  );
}
