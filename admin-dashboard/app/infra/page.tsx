"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { getInfrastructure, type InfraResponse, type NodeInfo } from "@/lib/api";
import { isAuthenticated, logout, getUser } from "@/lib/auth";
import StatsCard from "@/components/stats-card";

const REFRESH_INTERVAL = 15_000;

function shortNode(name: string): string {
  return name.replace(".ap-northeast-2.compute.internal", "");
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    Ready: "bg-green-100 text-green-700",
    Running: "bg-green-100 text-green-700",
    Pending: "bg-yellow-100 text-yellow-700",
    NotReady: "bg-red-100 text-red-700",
  };
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${colors[status] ?? "bg-gray-100 text-gray-600"}`}>
      {status}
    </span>
  );
}

function NodeCard({ node }: { node: NodeInfo }) {
  const hasPods = node.pods.length > 0;
  return (
    <div className={`rounded-lg border ${hasPods ? "border-blue-200 bg-white" : "border-gray-200 bg-gray-50"} shadow-sm`}>
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <div>
          <span className="text-sm font-semibold text-gray-900">{shortNode(node.node_name)}</span>
          <span className="ml-2 rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
            {node.instance_type}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={node.status} />
          <span className="text-xs text-gray-400">
            CPU {node.cpu_capacity} / Mem {node.memory_capacity}
          </span>
        </div>
      </div>

      {hasPods ? (
        <div className="divide-y divide-gray-100">
          {node.pods.map((pod) => (
            <div key={pod.pod_name} className="flex items-center justify-between px-4 py-2.5">
              <div>
                <span className="text-sm font-medium text-gray-900">
                  {pod.user_name ?? pod.username}
                </span>
                <span className="ml-1 text-xs text-gray-400">({pod.username})</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-500">
                  CPU {pod.cpu_request} / Mem {pod.memory_request}
                </span>
                <StatusBadge status={pod.status} />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-4 py-4 text-center text-xs text-gray-400">
          Pod 없음 (가용)
        </div>
      )}
    </div>
  );
}

export default function InfraPage() {
  const router = useRouter();
  const [data, setData] = useState<InfraResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const res = await getInfrastructure();
      setData(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
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

  const user = getUser();

  const nodesWithPods = data?.nodes.filter((n) => n.pods.length > 0).length ?? 0;
  const nodesEmpty = data ? data.total_nodes - nodesWithPods : 0;

  return (
    <div className="min-h-screen bg-gray-50/50">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
          <h1 className="text-lg font-bold text-gray-900">Claude Code Admin</h1>
          <div className="flex items-center gap-6">
            <nav className="flex gap-4 text-sm font-medium text-gray-600">
              <Link href="/dashboard" className="hover:text-gray-900 transition-colors">
                대시보드
              </Link>
              <Link href="/users" className="hover:text-gray-900 transition-colors">
                사용자 관리
              </Link>
              <Link href="/usage" className="hover:text-gray-900 transition-colors">
                토큰 사용량
              </Link>
              <Link href="/infra" className="text-blue-600 border-b-2 border-blue-600 pb-0.5">
                인프라
              </Link>
            </nav>
            <div className="flex items-center gap-3">
              {user && <span className="text-sm text-gray-500">{user.name}</span>}
              <button
                onClick={logout}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
              >
                로그아웃
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}

        {data && (
          <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-4">
            <StatsCard label="총 노드" value={data.total_nodes} />
            <StatsCard label="활성 노드" value={nodesWithPods} />
            <StatsCard label="가용 노드" value={nodesEmpty} />
            <StatsCard label="총 Pod" value={data.total_pods} />
          </div>
        )}

        {data && (
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-900">노드 / Pod 현황</h2>
            <p className="text-xs text-gray-400">
              수집: {new Date(data.collected_at).toLocaleString("ko-KR")} / 15초 자동 갱신
            </p>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-12 text-gray-400">
            데이터를 불러오는 중...
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {data?.nodes.map((node) => (
              <NodeCard key={node.node_name} node={node} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
