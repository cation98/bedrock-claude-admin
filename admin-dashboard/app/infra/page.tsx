"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  getInfrastructure,
  getUsers,
  getNodeGroups,
  scaleNodeGroup,
  assignPod,
  movePod,
  terminatePod,
  drainNode,
  getInfraTemplates,
  getInfraAssignments,
  assignInfraPolicy,
  createInfraTemplate,
  updateInfraTemplate,
  deleteInfraTemplate,
  type InfraResponse,
  type NodeInfo,
  type User,
  type NodeGroupInfo,
  type InfraTemplateItem,
  type InfraTemplatePolicy,
  type InfraAssignment,
} from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import StatsCard from "@/components/stats-card";
import Pagination from "@/components/pagination";

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
          {!isSystem && (
            <button
              disabled={node.pods.filter((p) => p.username !== "SYSTEM").length > 0}
              onClick={async () => {
                if (confirm(`${shortNode(node.node_name)} 노드를 종료합니다.\n노드그룹 크기가 1 줄어듭니다.`)) {
                  try {
                    await drainNode(node.node_name);
                    onAction();
                  } catch (err) { alert(err instanceof Error ? err.message : "노드 종료 실패"); }
                }
              }}
              className="rounded bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600 hover:bg-red-100 disabled:opacity-30 disabled:cursor-not-allowed"
              title={node.pods.filter((p) => p.username !== "SYSTEM").length > 0 ? "Pod을 먼저 종료하세요" : "노드 종료"}
            >
              노드 종료
            </button>
          )}
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

  /* ── Existing infra state ── */
  const [data, setData] = useState<InfraResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [approvedUsers, setApprovedUsers] = useState<User[]>([]);
  const [assignUser, setAssignUser] = useState("");
  const [assignNode, setAssignNode] = useState("");
  const [nodeGroups, setNodeGroups] = useState<NodeGroupInfo[]>([]);

  /* ── Left panel: user policy assignments ── */
  const [assignments, setAssignments] = useState<InfraAssignment[]>([]);
  const [checkedUsers, setCheckedUsers] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [assignPage, setAssignPage] = useState(1);
  const PAGE_SIZE = 10;

  /* ── Right panel: template management ── */
  const [infraTemplates, setInfraTemplates] = useState<InfraTemplateItem[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const [editDirty, setEditDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  /* ── Edit form state ── */
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editNodegroup, setEditNodegroup] = useState("bedrock-claude-nodes");
  const [editMaxPods, setEditMaxPods] = useState(3);
  const [editCpuReq, setEditCpuReq] = useState("500m");
  const [editCpuLim, setEditCpuLim] = useState("1000m");
  const [editMemReq, setEditMemReq] = useState("1.5Gi");
  const [editMemLim, setEditMemLim] = useState("3Gi");
  const [editSharedWritable, setEditSharedWritable] = useState(false);

  /* ── Refs to prevent stale closures in interval ── */
  const editDirtyRef = useRef(editDirty);
  editDirtyRef.current = editDirty;

  /* ── Selected template object ── */
  const selectedTemplateObj = infraTemplates.find((t) => t.name === selectedTemplate);

  /* ── Load template data into form ── */
  const loadTemplateIntoForm = useCallback((t: InfraTemplateItem) => {
    setEditName(t.name);
    setEditDesc(t.description);
    setEditNodegroup(t.policy?.nodegroup ?? "bedrock-claude-nodes");
    setEditMaxPods(t.policy?.max_pods_per_node ?? 3);
    setEditCpuReq(t.policy?.cpu_request ?? "500m");
    setEditCpuLim(t.policy?.cpu_limit ?? "1000m");
    setEditMemReq(t.policy?.memory_request ?? "1.5Gi");
    setEditMemLim(t.policy?.memory_limit ?? "3Gi");
    setEditSharedWritable(t.policy?.shared_dir_writable ?? false);
    setEditDirty(false);
  }, []);

  /* ── Clear form for new template creation ── */
  const clearForm = useCallback(() => {
    setEditName("");
    setEditDesc("");
    setEditNodegroup("bedrock-claude-nodes");
    setEditMaxPods(3);
    setEditCpuReq("500m");
    setEditCpuLim("1000m");
    setEditMemReq("1.5Gi");
    setEditMemLim("3Gi");
    setEditSharedWritable(false);
    setEditDirty(false);
  }, []);

  /* ── Build policy object from form ── */
  function buildPolicy(): InfraTemplatePolicy {
    return {
      nodegroup: editNodegroup,
      node_selector: null,
      max_pods_per_node: editMaxPods,
      cpu_request: editCpuReq,
      cpu_limit: editCpuLim,
      memory_request: editMemReq,
      memory_limit: editMemLim,
      shared_dir_writable: editSharedWritable,
    };
  }

  /* ── Data fetch ── */
  const fetchData = useCallback(async () => {
    try {
      const [res, usersRes, ngRes, templatesRes, assignRes] = await Promise.all([
        getInfrastructure(),
        getUsers(),
        getNodeGroups(),
        getInfraTemplates().catch(() => ({ templates: [] as InfraTemplateItem[] })),
        getInfraAssignments().catch(() => ({ assignments: [] as InfraAssignment[] })),
      ]);
      setData(res);
      setApprovedUsers(usersRes.users.filter((u) => u.is_approved));
      setNodeGroups(ngRes.groups);
      setInfraTemplates(templatesRes.templates);
      setAssignments(assignRes.assignments);
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
    const timer = setInterval(() => {
      if (!editDirtyRef.current) fetchData();
    }, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [router, fetchData]);

  /* ── Template selection ── */
  useEffect(() => {
    if (selectedTemplate && !isCreatingNew) {
      const t = infraTemplates.find((tpl) => tpl.name === selectedTemplate);
      if (t) loadTemplateIntoForm(t);
    }
  }, [selectedTemplate, infraTemplates, isCreatingNew, loadTemplateIntoForm]);

  /* ── Save template handler ── */
  async function handleSaveTemplate() {
    if (!editName.trim()) { setError("정책명을 입력하세요"); return; }
    setSaving(true);
    try {
      const policy = buildPolicy();
      if (isCreatingNew) {
        await createInfraTemplate({ name: editName.trim(), description: editDesc.trim(), policy });
        setIsCreatingNew(false);
        setSelectedTemplate(editName.trim());
      } else if (selectedTemplateObj?.id) {
        await updateInfraTemplate(selectedTemplateObj.id, { name: editName.trim(), description: editDesc.trim(), policy });
      } else {
        // built-in: create as custom override
        await createInfraTemplate({ name: editName.trim(), description: editDesc.trim(), policy });
      }
      setEditDirty(false);
      setSuccess("저장 완료");
      setTimeout(() => setSuccess(""), 2000);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  }

  /* ── Delete template handler ── */
  async function handleDeleteTemplate() {
    if (!selectedTemplateObj?.id) return;
    if (!confirm(`"${selectedTemplateObj.name}" 정책을 삭제하시겠습니까?`)) return;
    try {
      await deleteInfraTemplate(selectedTemplateObj.id);
      setSelectedTemplate(null);
      clearForm();
      setSuccess("삭제 완료");
      setTimeout(() => setSuccess(""), 2000);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "삭제 실패");
    }
  }

  /* ── Bulk policy assignment handler ── */
  async function handleBulkAssign(templateName: string) {
    const usernames = Array.from(checkedUsers);
    if (usernames.length === 0) return;
    try {
      await assignInfraPolicy({ usernames, template_name: templateName });
      setCheckedUsers(new Set());
      setSuccess(`${usernames.length}명에게 "${templateName}" 정책 적용 완료`);
      setTimeout(() => setSuccess(""), 2000);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "일괄 적용 실패");
    }
  }

  const nodesWithPods = data?.nodes.filter((n) => n.pods.length > 0).length ?? 0;
  const nodesEmpty = data ? data.total_nodes - nodesWithPods : 0;

  /* ── Filter assignments by search query ── */
  const filteredAssignments = assignments.filter((a) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      a.username.toLowerCase().includes(q) ||
      (a.name ?? "").toLowerCase().includes(q) ||
      a.infra_policy_name.toLowerCase().includes(q)
    );
  });

  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => { setAssignPage(1); }, [searchQuery]);

  const assignTotalPages = Math.max(1, Math.ceil(filteredAssignments.length / PAGE_SIZE));
  const assignSafePage = Math.min(assignPage, assignTotalPages);
  const paginatedAssignments = filteredAssignments.slice((assignSafePage - 1) * PAGE_SIZE, assignSafePage * PAGE_SIZE);

  /* ── Nodegroup names for dropdown ── */
  const nodegroupNames = nodeGroups.map((ng) => ng.name);

  return (
    <>
      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
        {error && (
          <div className="mb-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}
        {success && (
          <div className="mb-4 rounded-md bg-green-50 px-4 py-3 text-sm text-green-700">{success}</div>
        )}

        {/* Stats Cards */}
        {data && (
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-4">
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
          <div className="flex gap-6">
            {/* ═══════ LEFT PANEL (60%) ═══════ */}
            <div className="flex-[3] min-w-0 space-y-4">

              {/* Node Group Scaling */}
              <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
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

              {/* Pod 할당 (simple) */}
              <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
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

              {/* ── User Infra Policy Table ── */}
              <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                  <h3 className="text-sm font-semibold text-gray-900">사용자 인프라 정책</h3>
                  <div className="flex items-center gap-2">
                    {checkedUsers.size > 0 && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-blue-600">{checkedUsers.size}명 선택</span>
                        <select
                          className="rounded-md border border-blue-300 bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700"
                          value=""
                          onChange={(e) => {
                            if (e.target.value) handleBulkAssign(e.target.value);
                          }}
                        >
                          <option value="">일괄 적용...</option>
                          {infraTemplates.map((t) => (
                            <option key={t.name} value={t.name}>{t.name}</option>
                          ))}
                        </select>
                      </div>
                    )}
                    <input
                      type="text"
                      placeholder="사용자 검색..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="rounded-md border border-gray-300 px-2.5 py-1 text-xs w-40"
                    />
                  </div>
                </div>
                <div className="overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-gray-50 text-xs text-gray-500 uppercase">
                      <tr>
                        <th className="px-4 py-2 text-left w-8">
                          <input
                            type="checkbox"
                            checked={filteredAssignments.length > 0 && checkedUsers.size === filteredAssignments.length}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setCheckedUsers(new Set(filteredAssignments.map((a) => a.username)));
                              } else {
                                setCheckedUsers(new Set());
                              }
                            }}
                            className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600"
                          />
                        </th>
                        <th className="px-4 py-2 text-left">사용자</th>
                        <th className="px-4 py-2 text-left">인프라 정책</th>
                        <th className="px-4 py-2 text-right">빠른 설정</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {filteredAssignments.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="px-4 py-6 text-center text-xs text-gray-400">
                            {searchQuery ? "검색 결과 없음" : "인프라 정책 데이터 없음"}
                          </td>
                        </tr>
                      ) : (
                        paginatedAssignments.map((user) => (
                          <tr key={user.user_id} className="hover:bg-gray-50/50">
                            <td className="px-4 py-2">
                              <input
                                type="checkbox"
                                checked={checkedUsers.has(user.username)}
                                onChange={(e) => {
                                  const next = new Set(checkedUsers);
                                  if (e.target.checked) next.add(user.username);
                                  else next.delete(user.username);
                                  setCheckedUsers(next);
                                }}
                                className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600"
                              />
                            </td>
                            <td className="px-4 py-2">
                              <span className="font-medium text-gray-900">{user.name ?? user.username}</span>
                              <span className="ml-1 text-xs text-gray-400">({user.username})</span>
                            </td>
                            <td className="px-4 py-2">
                              <span className="inline-flex items-center rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
                                {user.infra_policy_name}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <select
                                className="rounded border border-gray-200 px-1.5 py-0.5 text-xs"
                                value=""
                                onChange={async (e) => {
                                  if (!e.target.value) return;
                                  try {
                                    await assignInfraPolicy({ usernames: [user.username], template_name: e.target.value });
                                    fetchData();
                                  } catch (err) { setError(err instanceof Error ? err.message : "정책 적용 실패"); }
                                }}
                              >
                                <option value="">현재: {user.infra_policy_name}</option>
                                {infraTemplates.map((t) => (
                                  <option key={t.name} value={t.name}>{t.name}</option>
                                ))}
                              </select>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  currentPage={assignSafePage}
                  totalPages={assignTotalPages}
                  totalItems={filteredAssignments.length}
                  itemsPerPage={PAGE_SIZE}
                  onPageChange={setAssignPage}
                />
              </div>

              {/* Node / Pod Status Cards */}
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {data?.nodes.map((node) => (
                  <NodeCard key={node.node_name} node={node} allNodes={data.nodes} onAction={fetchData} />
                ))}
              </div>
            </div>

            {/* ═══════ RIGHT PANEL (40%) ═══════ */}
            <div className="flex-[2] min-w-0">
              <div className="sticky top-6 rounded-lg border border-gray-200 bg-white shadow-sm">
                {/* Header */}
                <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                  <h3 className="text-sm font-semibold text-gray-900">인프라 정책 관리</h3>
                  <button
                    onClick={() => {
                      setIsCreatingNew(true);
                      setSelectedTemplate(null);
                      clearForm();
                    }}
                    className="rounded-md bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 transition-colors"
                  >
                    + 새 정책
                  </button>
                </div>

                {/* Template list */}
                <div className="max-h-[180px] overflow-y-auto border-b border-gray-200">
                  {infraTemplates.length === 0 ? (
                    <div className="px-4 py-6 text-center text-xs text-gray-400">
                      정책 템플릿이 없습니다
                    </div>
                  ) : (
                    infraTemplates.map((t) => {
                      const isActive = !isCreatingNew && selectedTemplate === t.name;
                      const assignedCount = assignments.filter((a) => a.infra_policy_name === t.name).length;
                      return (
                        <button
                          key={t.name}
                          onClick={() => {
                            setIsCreatingNew(false);
                            setSelectedTemplate(t.name);
                          }}
                          className={`flex w-full items-center justify-between px-4 py-2.5 text-left transition-colors ${
                            isActive ? "bg-blue-50 border-l-2 border-blue-600" : "hover:bg-gray-50 border-l-2 border-transparent"
                          }`}
                        >
                          <div>
                            <span className={`text-sm font-medium ${isActive ? "text-blue-700" : "text-gray-900"}`}>
                              {t.name}
                            </span>
                            {t.is_builtin && (
                              <span className="ml-1.5 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                                기본
                              </span>
                            )}
                            <p className="text-xs text-gray-400 mt-0.5 truncate max-w-[200px]">{t.description}</p>
                          </div>
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                            {assignedCount}명
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>

                {/* Detail / Edit form */}
                {(selectedTemplate || isCreatingNew) && (
                  <div className="space-y-3 p-4">
                    {isCreatingNew && (
                      <div className="rounded-md bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700">
                        새 정책 생성
                      </div>
                    )}

                    {/* 정책명 */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                        정책명
                      </label>
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => { setEditName(e.target.value); setEditDirty(true); }}
                        disabled={!isCreatingNew && selectedTemplateObj?.is_builtin}
                        className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:bg-gray-50 disabled:text-gray-500"
                        placeholder="예: standard, premium"
                      />
                    </div>

                    {/* 설명 */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                        설명
                      </label>
                      <input
                        type="text"
                        value={editDesc}
                        onChange={(e) => { setEditDesc(e.target.value); setEditDirty(true); }}
                        className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                        placeholder="정책 설명"
                      />
                    </div>

                    {/* 노드그룹 */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                        노드그룹
                      </label>
                      <select
                        value={editNodegroup}
                        onChange={(e) => { setEditNodegroup(e.target.value); setEditDirty(true); }}
                        className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                      >
                        {nodegroupNames.length > 0 ? (
                          nodegroupNames.map((name) => (
                            <option key={name} value={name}>{name}</option>
                          ))
                        ) : (
                          <option value="bedrock-claude-nodes">bedrock-claude-nodes</option>
                        )}
                      </select>
                    </div>

                    {/* 노드당 Pod 수 */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                        노드당 Pod 수
                      </label>
                      <input
                        type="number"
                        min={1}
                        max={20}
                        value={editMaxPods}
                        onChange={(e) => { setEditMaxPods(Number(e.target.value)); setEditDirty(true); }}
                        className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                      />
                    </div>

                    {/* CPU / Memory grid */}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                          CPU Request
                        </label>
                        <input
                          type="text"
                          value={editCpuReq}
                          onChange={(e) => { setEditCpuReq(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                          placeholder="500m"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                          CPU Limit
                        </label>
                        <input
                          type="text"
                          value={editCpuLim}
                          onChange={(e) => { setEditCpuLim(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                          placeholder="1000m"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                          Mem Request
                        </label>
                        <input
                          type="text"
                          value={editMemReq}
                          onChange={(e) => { setEditMemReq(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                          placeholder="1.5Gi"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                          Mem Limit
                        </label>
                        <input
                          type="text"
                          value={editMemLim}
                          onChange={(e) => { setEditMemLim(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                          placeholder="3Gi"
                        />
                      </div>
                    </div>

                    {/* 공유 디렉토리 쓰기 */}
                    <div>
                      <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={editSharedWritable}
                          onChange={(e) => { setEditSharedWritable(e.target.checked); setEditDirty(true); }}
                          className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                        />
                        공유 디렉토리 쓰기
                      </label>
                    </div>

                    {/* Action Buttons */}
                    <div className="flex gap-2 pt-2">
                      <button
                        onClick={handleSaveTemplate}
                        disabled={saving}
                        className="flex-1 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
                      >
                        {saving ? "저장 중..." : isCreatingNew ? "생성" : "저장"}
                      </button>
                      {!isCreatingNew && selectedTemplateObj && !selectedTemplateObj.is_builtin && (
                        <button
                          onClick={handleDeleteTemplate}
                          className="rounded-md border border-red-300 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 transition-colors"
                        >
                          삭제
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {/* Empty state when nothing selected */}
                {!selectedTemplate && !isCreatingNew && (
                  <div className="px-4 py-8 text-center text-xs text-gray-400">
                    좌측 목록에서 정책을 선택하거나<br />
                    &quot;+ 새 정책&quot; 버튼으로 생성하세요
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </>
  );
}
