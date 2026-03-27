"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { getInfrastructure, getUsers, getNodeGroups, scaleNodeGroup, assignPod, movePod, terminatePod, type InfraResponse, type NodeInfo, type User, type NodeGroupInfo } from "@/lib/api";
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

const ROLE_BADGE: Record<string, { bg: string; label: string }> = {
  system: { bg: "bg-red-100 text-red-700", label: "시스템 (삭제 금지)" },
  presenter: { bg: "bg-purple-100 text-purple-700", label: "전용 노드" },
  user: { bg: "bg-blue-50 text-blue-700", label: "사용자" },
};

function NodeCard({ node, allNodes, onAction }: { node: NodeInfo; allNodes: NodeInfo[]; onAction: () => void }) {
  const hasPods = node.pods.length > 0;
  const isSystem = node.node_role === "system";
  const role = ROLE_BADGE[node.node_role] ?? ROLE_BADGE.user;
  return (
    <div className={`rounded-lg border ${isSystem ? "border-red-200 bg-red-50/30" : hasPods ? "border-blue-200 bg-white" : "border-gray-200 bg-gray-50"} shadow-sm`}>
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <div>
          <span className="text-sm font-semibold text-gray-900">{shortNode(node.node_name)}</span>
          <span className="ml-2 rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
            {node.instance_type}
          </span>
          <span className={`ml-2 rounded px-2 py-0.5 text-xs font-medium ${role.bg}`}>
            {role.label}
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
                {pod.username !== "SYSTEM" && (
                  <div className="flex gap-1">
                    <select
                      className="rounded border border-gray-200 px-1 py-0.5 text-xs"
                      defaultValue=""
                      onChange={async (e) => {
                        const target = e.target.value;
                        if (!target) return;
                        if (confirm(`${pod.user_name ?? pod.username} Pod을 이동하시겠습니까?\n대화는 자동 백업됩니다.`)) {
                          try {
                            await movePod(pod.username, target);
                            onAction();
                          } catch (err) { alert(err instanceof Error ? err.message : "이동 실패"); }
                        }
                        e.target.value = "";
                      }}
                    >
                      <option value="">이동</option>
                      {allNodes.filter((n) => n.node_name !== node.node_name && n.node_role !== "system").map((n) => (
                        <option key={n.node_name} value={n.node_name}>{shortNode(n.node_name)} ({n.instance_type})</option>
                      ))}
                    </select>
                    <button
                      onClick={async () => {
                        if (confirm(`${pod.user_name ?? pod.username} Pod을 종료하시겠습니까?`)) {
                          try {
                            await terminatePod(pod.username);
                            onAction();
                          } catch (err) { alert(err instanceof Error ? err.message : "종료 실패"); }
                        }
                      }}
                      className="rounded bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600 hover:bg-red-100"
                    >
                      종료
                    </button>
                  </div>
                )}
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
  const [approvedUsers, setApprovedUsers] = useState<User[]>([]);
  const [assignUser, setAssignUser] = useState("");
  const [assignNode, setAssignNode] = useState("");
  const [nodeGroups, setNodeGroups] = useState<NodeGroupInfo[]>([]);

  const fetchData = useCallback(async () => {
    try {
      const [res, usersRes, ngRes] = await Promise.all([getInfrastructure(), getUsers(), getNodeGroups()]);
      setData(res);
      setApprovedUsers(usersRes.users.filter((u) => u.is_approved));
      setNodeGroups(ngRes.groups);
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
          <>
          {/* Node Group Scaling */}
          <div className="mb-4 rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-gray-900">노드그룹 관리</h3>
            </div>
            <div className="divide-y divide-gray-100">
              {nodeGroups.map((ng) => (
                <div key={ng.name} className="flex items-center justify-between px-4 py-3">
                  <div>
                    <span className="text-sm font-medium text-gray-900">{ng.name}</span>
                    <span className="ml-2 rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">{ng.instance_type}</span>
                    <span className={`ml-2 rounded px-2 py-0.5 text-xs font-medium ${ng.status === "ACTIVE" ? "bg-green-100 text-green-700" : "bg-yellow-100 text-yellow-700"}`}>
                      {ng.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-500">
                      현재 {ng.desired_size}대 (min {ng.min_size} / max {ng.max_size})
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={async () => {
                          if (ng.desired_size <= 0) return;
                          const newSize = ng.desired_size - 1;
                          if (confirm(`${ng.name} 노드를 ${ng.desired_size}대 → ${newSize}대로 축소합니다.\n시스템 Pod이 있는 노드가 제거될 수 있으니 주의하세요.`)) {
                            try { await scaleNodeGroup(ng.name, newSize); fetchData(); } catch (err) { setError(err instanceof Error ? err.message : "스케일링 실패"); }
                          }
                        }}
                        disabled={ng.desired_size <= 0}
                        className="rounded border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-30"
                      >
                        -1
                      </button>
                      <span className="w-8 text-center text-sm font-bold">{ng.desired_size}</span>
                      <button
                        onClick={async () => {
                          const newSize = ng.desired_size + 1;
                          if (newSize > ng.max_size) { setError(`최대 ${ng.max_size}대까지 가능합니다`); return; }
                          try { await scaleNodeGroup(ng.name, newSize); fetchData(); } catch (err) { setError(err instanceof Error ? err.message : "스케일링 실패"); }
                        }}
                        disabled={ng.desired_size >= ng.max_size}
                        className="rounded border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-30"
                      >
                        +1
                      </button>
                      {ng.desired_size > 0 && (
                        <button
                          onClick={async () => {
                            if (confirm(`${ng.name} 노드를 모두 종료(0대)합니다.\n시스템 Pod이 있으면 서비스 장애가 발생합니다!`)) {
                              try { await scaleNodeGroup(ng.name, 0); fetchData(); } catch (err) { setError(err instanceof Error ? err.message : "스케일링 실패"); }
                            }
                          }}
                          className="ml-1 rounded bg-red-50 px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-100"
                        >
                          전체 종료
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Pod 할당 */}
          <div className="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <h3 className="mb-2 text-sm font-semibold text-gray-900">Pod 할당</h3>
            <div className="flex gap-2">
              <select
                value={assignUser}
                onChange={(e) => setAssignUser(e.target.value)}
                className="flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
              >
                <option value="">사용자 선택</option>
                {approvedUsers
                  .filter((u) => !data?.nodes.some((n) => n.pods.some((p) => p.username === u.username)))
                  .map((u) => (
                    <option key={u.username} value={u.username}>{u.name ?? u.username} ({u.username})</option>
                  ))}
              </select>
              <select
                value={assignNode}
                onChange={(e) => setAssignNode(e.target.value)}
                className="flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
              >
                <option value="">노드 자동 배치</option>
                {data?.nodes.filter((n) => n.node_role !== "system").map((n) => (
                  <option key={n.node_name} value={n.node_name}>
                    {shortNode(n.node_name)} ({n.instance_type}) - Pod {n.pods.filter((p) => p.username !== "SYSTEM").length}개
                  </option>
                ))}
              </select>
              <button
                disabled={!assignUser}
                onClick={async () => {
                  if (!assignUser) return;
                  try {
                    await assignPod(assignUser, assignNode || undefined);
                    setAssignUser("");
                    setAssignNode("");
                    fetchData();
                  } catch (err) { setError(err instanceof Error ? err.message : "할당 실패"); }
                }}
                className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                할당
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {data?.nodes.map((node) => (
              <NodeCard key={node.node_name} node={node} allNodes={data.nodes} onAction={fetchData} />
            ))}
          </div>
          </>
        )}
      </main>
    </div>
  );
}
