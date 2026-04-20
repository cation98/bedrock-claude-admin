"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  getInfrastructure,
  getUsers,
  getNodeGroups,
  scaleNodeGroup,
  assignPod,
  terminatePod,
  drainNode,
  getInfraTemplates,
  getInfraAssignments,
  assignInfraPolicy,
  createInfraTemplate,
  updateInfraTemplate,
  deleteInfraTemplate,
  getUnhealthyPods,
  deleteUnhealthyDeployment,
  type InfraResponse,
  type NodeInfo,
  type User,
  type NodeGroupInfo,
  type InfraTemplateItem,
  type InfraTemplatePolicy,
  type InfraAssignment,
  type UnhealthyPod,
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
    Ready: "bg-[var(--success-light)] text-[var(--success)]",
    Running: "bg-[var(--success-light)] text-[var(--success)]",
    Pending: "bg-[var(--warning-light)] text-[var(--warning)]",
    NotReady: "bg-[var(--danger-light)] text-[var(--danger)]",
  };
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${colors[status] ?? "bg-[var(--surface-hover)] text-[var(--text-secondary)]"}`}>
      {status}
    </span>
  );
}

const ROLE_BADGE: Record<string, { bg: string; label: string }> = {
  system:      { bg: "bg-[var(--danger-light)] text-[var(--danger)]",    label: "시스템 (삭제 금지)" },
  gitea:       { bg: "bg-purple-100 text-purple-700",                    label: "Gitea / OnlyOffice" },
  presenter:   { bg: "bg-[var(--info-light)] text-[var(--info)]",        label: "전용 노드" },
  workload:    { bg: "bg-[var(--warning-light)] text-[var(--warning)]",  label: "OpenWebUI 워크로드 전용" },
  "user-apps": { bg: "bg-[var(--success-light)] text-[var(--success)]",  label: "사용자 배포 앱 전용" },
  terminal:    { bg: "bg-[var(--primary-light)] text-[var(--primary)]",  label: "사용자 터미널 전용" },
  user:        { bg: "bg-[var(--surface-hover)] text-[var(--text-muted)]", label: "사용자" },
};

const POD_KIND_BADGE: Record<string, string> = {
  terminal: "bg-[var(--primary-light)] text-[var(--primary)]",
  workload: "bg-[var(--warning-light)] text-[var(--warning)]",
  system:   "bg-[var(--danger-light)] text-[var(--danger)]",
  dummy:    "bg-[var(--surface-hover)] text-[var(--text-muted)]",
};

function NodeCard({ node, onAction }: { node: NodeInfo; onAction: () => void }) {
  const hasPods = node.pods.length > 0;
  const isSystem = node.node_role === "system";
  const isGitea  = node.node_role === "gitea";
  const role = ROLE_BADGE[node.node_role] ?? ROLE_BADGE.user;
  return (
    <div className={`rounded-lg border ${isSystem ? "border-[var(--danger-light)] bg-[var(--danger-light)]/30" : isGitea ? "border-purple-200 bg-purple-50" : hasPods ? "border-[var(--border)] bg-[var(--surface)]" : "border-[var(--border)] bg-[var(--bg)]"} shadow-sm`}>
      <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
        <div>
          <span className="text-sm font-semibold text-[var(--text-primary)]">{shortNode(node.node_name)}</span>
          <span className="ml-2 rounded bg-[var(--primary-light)] px-2 py-0.5 text-xs font-medium text-[var(--primary)]">
            {node.instance_type}
          </span>
          <span className={`ml-2 rounded px-2 py-0.5 text-xs font-medium ${role.bg}`}>
            {role.label}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={node.status} />
          <span className="text-xs text-[var(--text-muted)]">
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
              className="rounded bg-[var(--danger-light)] px-2 py-0.5 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] disabled:opacity-30 disabled:cursor-not-allowed"
              title={node.pods.filter((p) => p.username !== "SYSTEM").length > 0 ? "Pod을 먼저 종료하세요" : "노드 종료"}
            >
              노드 종료
            </button>
          )}
        </div>
      </div>

      {hasPods ? (
        <div className="divide-y divide-[var(--border)]">
          {node.pods.map((pod) => {
            const kindBadgeCls = POD_KIND_BADGE[pod.pod_kind ?? "system"] ?? POD_KIND_BADGE.system;
            const isTerminal = pod.pod_kind === "terminal";
            const isWorkload = pod.pod_kind === "workload";
            const isDummy = pod.pod_kind === "dummy";
            return (
              <div key={pod.pod_name} className={`flex items-center justify-between px-4 py-2 ${isWorkload ? "bg-[var(--warning-light)]/10" : isDummy ? "opacity-50" : ""}`}>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${kindBadgeCls}`}>
                      {isTerminal ? "터미널" : isWorkload ? "워크로드" : isDummy ? "예약석" : "시스템"}
                    </span>
                    {pod.namespace && (
                      <span className="text-[10px] text-[var(--text-muted)] font-mono">{pod.namespace}</span>
                    )}
                    <span className="text-sm font-medium text-[var(--text-primary)] truncate">
                      {pod.user_name ?? pod.username}
                    </span>
                    {pod.pod_ip && (
                      <span className="text-[10px] font-mono text-[var(--text-muted)]">{pod.pod_ip}</span>
                    )}
                  </div>
                  {isWorkload && (
                    <div className="mt-0.5 text-[10px] font-mono text-[var(--text-muted)] truncate">{pod.pod_name}</div>
                  )}
                </div>
                <div className="flex items-center gap-2 ml-2 shrink-0">
                  <span className="text-xs text-[var(--text-muted)] whitespace-nowrap">
                    {pod.cpu_request} / {pod.memory_request}
                  </span>
                  <StatusBadge status={pod.status} />
                  {isTerminal && (
                    <button
                      onClick={async () => {
                        if (confirm(`${pod.user_name ?? pod.username} Pod을 종료하시겠습니까?`)) {
                          try {
                            await terminatePod(pod.username);
                            onAction();
                          } catch (err) { alert(err instanceof Error ? err.message : "종료 실패"); }
                        }
                      }}
                      className="rounded bg-[var(--danger-light)] px-2 py-0.5 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)]"
                    >
                      종료
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="px-4 py-4 text-center text-xs text-[var(--text-muted)]">
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
  const [assignSearch, setAssignSearch] = useState("");
  const [assignDropOpen, setAssignDropOpen] = useState(false);
  const [nodeGroups, setNodeGroups] = useState<NodeGroupInfo[]>([]);
  const [unhealthy, setUnhealthy] = useState<UnhealthyPod[]>([]);
  const [unhealthyAt, setUnhealthyAt] = useState<string>("");
  const [deletingDeploy, setDeletingDeploy] = useState<string>("");

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
      const [res, usersRes, ngRes, templatesRes, assignRes, unhealthyRes] = await Promise.all([
        getInfrastructure(),
        getUsers(),
        getNodeGroups(),
        getInfraTemplates().catch(() => ({ templates: [] as InfraTemplateItem[] })),
        getInfraAssignments().catch(() => ({ assignments: [] as InfraAssignment[] })),
        getUnhealthyPods().catch(() => ({ pods: [] as UnhealthyPod[], collected_at: "" })),
      ]);
      setData(res);
      setApprovedUsers(usersRes.users.filter((u) => u.is_approved));
      setNodeGroups(ngRes.groups);
      setInfraTemplates(templatesRes.templates);
      setAssignments(assignRes.assignments);
      setUnhealthy(unhealthyRes.pods);
      setUnhealthyAt(unhealthyRes.collected_at);
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
          <div className="mb-4 rounded-md bg-[var(--error-light)] px-4 py-3 text-sm text-[var(--error)]">{error}</div>
        )}
        {success && (
          <div className="mb-4 rounded-md bg-[var(--success-light)] px-4 py-3 text-sm text-[var(--success)]">{success}</div>
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

        {/* Unhealthy Pods — 실시간 비정상 Pod 모니터링 */}
        {unhealthy.length > 0 && (
          <div className="mb-6 rounded-lg border border-[var(--danger-light)] bg-[var(--surface)] shadow-sm">
            <div className="flex items-center justify-between border-b border-[var(--danger-light)] bg-[var(--danger-light)]/30 px-4 py-3">
              <div>
                <h3 className="text-sm font-semibold text-[var(--danger)]">
                  ⚠ 비정상 Pod ({unhealthy.length})
                </h3>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                  CrashLoopBackOff / ImagePullBackOff / Pending(5m+) / Restart≥5 · {unhealthyAt && new Date(unhealthyAt).toLocaleTimeString("ko-KR")}
                </p>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-[var(--surface-hover)] text-[var(--text-secondary)]">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">NS</th>
                    <th className="px-3 py-2 text-left font-medium">Pod</th>
                    <th className="px-3 py-2 text-left font-medium">IP</th>
                    <th className="px-3 py-2 text-left font-medium">Node</th>
                    <th className="px-3 py-2 text-left font-medium">Status</th>
                    <th className="px-3 py-2 text-right font-medium">Restarts</th>
                    <th className="px-3 py-2 text-left font-medium">Age</th>
                    <th className="px-3 py-2 text-left font-medium">Owner</th>
                    <th className="px-3 py-2 text-right font-medium">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {unhealthy.map((p) => {
                    const ageH = Math.floor(p.age_seconds / 3600);
                    const ageM = Math.floor((p.age_seconds % 3600) / 60);
                    const deployKey = `${p.namespace}/${p.deployment ?? ""}`;
                    const canDelete = !!p.deployment;
                    return (
                      <tr key={p.pod_name} className="hover:bg-[var(--surface-hover)]">
                        <td className="px-3 py-2 font-mono">{p.namespace}</td>
                        <td className="px-3 py-2 font-mono text-[var(--text-primary)]" title={p.message ?? ""}>
                          {p.pod_name}
                        </td>
                        <td className="px-3 py-2 font-mono text-[var(--text-secondary)]">{p.pod_ip ?? "-"}</td>
                        <td className="px-3 py-2 font-mono text-[var(--text-secondary)]">{p.node_name ? shortNode(p.node_name) : "-"}</td>
                        <td className="px-3 py-2">
                          <span className="inline-block rounded-full bg-[var(--danger-light)] px-2 py-0.5 text-xs font-medium text-[var(--danger)]">
                            {p.status}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right font-mono">{p.restarts}</td>
                        <td className="px-3 py-2 font-mono text-[var(--text-secondary)]">
                          {ageH > 0 ? `${ageH}h${ageM}m` : `${ageM}m`}
                        </td>
                        <td className="px-3 py-2 font-mono text-[var(--text-secondary)]">
                          {p.owner ?? "-"}{p.app_name ? ` / ${p.app_name}` : ""}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {canDelete ? (
                            <button
                              disabled={deletingDeploy === deployKey}
                              onClick={async () => {
                                if (!confirm(`Deployment 삭제: ${p.namespace}/${p.deployment}\n연관 Service도 함께 삭제됩니다.`)) return;
                                setDeletingDeploy(deployKey);
                                try {
                                  await deleteUnhealthyDeployment(p.namespace, p.deployment!);
                                  setSuccess(`${p.namespace}/${p.deployment} 삭제됨`);
                                  fetchData();
                                } catch (err) {
                                  setError(err instanceof Error ? err.message : "삭제 실패");
                                } finally {
                                  setDeletingDeploy("");
                                }
                              }}
                              className="rounded-md border border-[var(--danger)] px-2 py-1 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] disabled:opacity-50"
                            >
                              {deletingDeploy === deployKey ? "삭제 중..." : "Deploy 삭제"}
                            </button>
                          ) : (
                            <span className="text-[var(--text-muted)]">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {data && (
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">노드 / Pod 현황</h2>
            <p className="text-xs text-[var(--text-muted)]">
              수집: {new Date(data.collected_at).toLocaleString("ko-KR")} / 15초 자동 갱신
            </p>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-12 text-[var(--text-muted)]">
            데이터를 불러오는 중...
          </div>
        ) : (
          <div className="flex gap-6">
            {/* ═══════ LEFT PANEL (60%) ═══════ */}
            <div className="flex-[3] min-w-0 space-y-4">

              {/* Node Group Scaling */}
              <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                <div className="border-b border-[var(--border)] px-4 py-3">
                  <h3 className="text-sm font-semibold text-[var(--text-primary)]">노드그룹 관리</h3>
                </div>
                <div className="divide-y divide-[var(--border)]">
                  {nodeGroups.map((ng) => (
                    <div key={ng.name} className="flex items-center justify-between px-4 py-3">
                      <div>
                        <span className="text-sm font-medium text-[var(--text-primary)]">{ng.name}</span>
                        <span className="ml-2 rounded bg-[var(--primary-light)] px-2 py-0.5 text-xs font-medium text-[var(--primary)]">{ng.instance_type}</span>
                        <span className={`ml-2 rounded px-2 py-0.5 text-xs font-medium ${ng.status === "ACTIVE" ? "bg-[var(--success-light)] text-[var(--success)]" : "bg-[var(--warning-light)] text-[var(--warning)]"}`}>
                          {ng.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-[var(--text-muted)]">
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
                            className="rounded border border-[var(--border-strong)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg)] disabled:opacity-30"
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
                            className="rounded border border-[var(--border-strong)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg)] disabled:opacity-30"
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
                              className="ml-1 rounded bg-[var(--danger-light)] px-2 py-1 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger-light)]"
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

              {/* Pod 할당 — 사용자 검색 + 노드 선택 */}
              <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 shadow-sm">
                <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Pod 할당</h3>
                <div className="flex gap-2 items-start">

                  {/* ── 사용자 검색 드롭다운 ── */}
                  <div className="relative flex-1">
                    <div
                      className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-sm cursor-text ${assignDropOpen || assignUser ? "border-[var(--primary)]" : "border-[var(--border-strong)]"}`}
                      onClick={() => { setAssignDropOpen(true); }}
                    >
                      {assignUser ? (
                        <>
                          <span className="font-medium text-[var(--text-primary)]">
                            {approvedUsers.find((u) => u.username === assignUser)?.name ?? assignUser}
                          </span>
                          <span className="text-xs text-[var(--text-muted)]">({assignUser})</span>
                          <button
                            onClick={(e) => { e.stopPropagation(); setAssignUser(""); setAssignSearch(""); }}
                            className="ml-auto text-[var(--text-muted)] hover:text-[var(--danger)]"
                          >✕</button>
                        </>
                      ) : (
                        <input
                          autoFocus={assignDropOpen}
                          placeholder="이름 또는 사번 검색..."
                          value={assignSearch}
                          onChange={(e) => { setAssignSearch(e.target.value); setAssignDropOpen(true); }}
                          onFocus={() => setAssignDropOpen(true)}
                          onBlur={() => setTimeout(() => setAssignDropOpen(false), 150)}
                          className="w-full bg-transparent outline-none placeholder-[var(--text-muted)] text-[var(--text-primary)]"
                        />
                      )}
                    </div>

                    {/* 드롭다운 목록 */}
                    {assignDropOpen && !assignUser && (
                      <div className="absolute z-50 mt-1 w-full max-h-56 overflow-y-auto rounded-md border border-[var(--border)] bg-[var(--surface)] shadow-lg">
                        {approvedUsers
                          .filter((u) => {
                            const q = assignSearch.toLowerCase();
                            const hasPod = data?.nodes.some((n) => n.pods.some((p) => p.username === u.username));
                            if (hasPod) return false; // 이미 Pod 있는 사용자 제외
                            if (!q) return true;
                            return (
                              u.username.toLowerCase().includes(q) ||
                              (u.name ?? "").toLowerCase().includes(q)
                            );
                          })
                          .slice(0, 20)
                          .map((u) => (
                            <button
                              key={u.username}
                              onMouseDown={() => {
                                setAssignUser(u.username);
                                setAssignSearch("");
                                setAssignDropOpen(false);
                              }}
                              className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-[var(--surface-hover)]"
                            >
                              <div>
                                <span className="font-medium text-[var(--text-primary)]">{u.name ?? u.username}</span>
                                <span className="ml-1 text-xs text-[var(--text-muted)]">({u.username})</span>
                              </div>
                              {u.team_name && (
                                <span className="text-[10px] text-[var(--text-muted)]">{u.team_name}</span>
                              )}
                            </button>
                          ))}
                        {approvedUsers.filter((u) => {
                          const q = assignSearch.toLowerCase();
                          const hasPod = data?.nodes.some((n) => n.pods.some((p) => p.username === u.username));
                          return !hasPod && (q ? u.username.toLowerCase().includes(q) || (u.name ?? "").toLowerCase().includes(q) : true);
                        }).length === 0 && (
                          <div className="px-3 py-2 text-xs text-[var(--text-muted)]">검색 결과 없음</div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* ── 노드 선택 ── */}
                  <select
                    value={assignNode}
                    onChange={(e) => setAssignNode(e.target.value)}
                    className="flex-1 rounded-md border border-[var(--border-strong)] px-2 py-1.5 text-sm text-[var(--text-primary)] bg-[var(--surface)]"
                  >
                    <option value="">노드 자동 배치</option>
                    {data?.nodes
                      .filter((n) => n.node_role === "terminal" || n.node_role === "user")
                      .map((n) => (
                        <option key={n.node_name} value={n.node_name}>
                          {shortNode(n.node_name)} ({n.instance_type}) — Pod {n.pods.filter((p) => p.pod_kind === "terminal").length}개
                        </option>
                      ))}
                  </select>

                  {/* ── 할당 버튼 ── */}
                  <button
                    disabled={!assignUser}
                    onClick={async () => {
                      if (!assignUser) return;
                      try {
                        await assignPod(assignUser, assignNode || undefined);
                        setAssignUser("");
                        setAssignNode("");
                        setAssignSearch("");
                        fetchData();
                      } catch (err) { setError(err instanceof Error ? err.message : "할당 실패"); }
                    }}
                    className="rounded-md bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-white hover:bg-[var(--primary-hover)] disabled:opacity-50 whitespace-nowrap"
                  >
                    할당
                  </button>
                </div>

                {/* 선택된 사용자 확인 */}
                {assignUser && (
                  <div className="mt-2 text-xs text-[var(--text-muted)]">
                    <span className="font-medium text-[var(--primary)]">
                      {approvedUsers.find((u) => u.username === assignUser)?.name ?? assignUser}
                    </span>
                    {" "}({assignUser}) →{" "}
                    {assignNode ? shortNode(assignNode) : "자동 배치"}
                  </div>
                )}
              </div>

              {/* ── User Infra Policy Table ── */}
              <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
                  <h3 className="text-sm font-semibold text-[var(--text-primary)]">사용자 인프라 정책</h3>
                  <div className="flex items-center gap-2">
                    {checkedUsers.size > 0 && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-[var(--primary)]">{checkedUsers.size}명 선택</span>
                        <select
                          className="rounded-md border border-[var(--primary)] bg-[var(--primary-light)] px-2 py-1 text-xs font-medium text-[var(--primary)]"
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
                      className="rounded-md border border-[var(--border-strong)] px-2.5 py-1 text-xs w-40"
                    />
                  </div>
                </div>
                <div className="overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-[var(--bg)] text-xs text-[var(--text-muted)] uppercase">
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
                            className="h-3.5 w-3.5 rounded border-[var(--border-strong)] text-[var(--primary)]"
                          />
                        </th>
                        <th className="px-4 py-2 text-left">사용자</th>
                        <th className="px-4 py-2 text-left">인프라 정책</th>
                        <th className="px-4 py-2 text-right">빠른 설정</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border)]">
                      {filteredAssignments.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="px-4 py-6 text-center text-xs text-[var(--text-muted)]">
                            {searchQuery ? "검색 결과 없음" : "인프라 정책 데이터 없음"}
                          </td>
                        </tr>
                      ) : (
                        paginatedAssignments.map((user) => (
                          <tr key={user.user_id} className="hover:bg-[var(--bg)]/50">
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
                                className="h-3.5 w-3.5 rounded border-[var(--border-strong)] text-[var(--primary)]"
                              />
                            </td>
                            <td className="px-4 py-2">
                              <span className="font-medium text-[var(--text-primary)]">{user.name ?? user.username}</span>
                              <span className="ml-1 text-xs text-[var(--text-muted)]">({user.username})</span>
                            </td>
                            <td className="px-4 py-2">
                              <span className="inline-flex items-center rounded-full bg-[var(--info-light)] px-2.5 py-0.5 text-xs font-medium text-[var(--info)]">
                                {user.infra_policy_name}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <select
                                className="rounded border border-[var(--border)] px-1.5 py-0.5 text-xs"
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
                  <NodeCard key={node.node_name} node={node} onAction={fetchData} />
                ))}
              </div>
            </div>

            {/* ═══════ RIGHT PANEL (40%) ═══════ */}
            <div className="flex-[2] min-w-0">
              <div className="sticky top-6 rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                {/* Header */}
                <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
                  <h3 className="text-sm font-semibold text-[var(--text-primary)]">인프라 정책 관리</h3>
                  <button
                    onClick={() => {
                      setIsCreatingNew(true);
                      setSelectedTemplate(null);
                      clearForm();
                    }}
                    className="rounded-md bg-[var(--primary)] px-2.5 py-1 text-xs font-medium text-white hover:bg-[var(--primary-hover)] transition-colors"
                  >
                    + 새 정책
                  </button>
                </div>

                {/* Template list */}
                <div className="max-h-[180px] overflow-y-auto border-b border-[var(--border)]">
                  {infraTemplates.length === 0 ? (
                    <div className="px-4 py-6 text-center text-xs text-[var(--text-muted)]">
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
                            isActive ? "bg-[var(--primary-light)] border-l-2 border-[var(--primary)]" : "hover:bg-[var(--bg)] border-l-2 border-transparent"
                          }`}
                        >
                          <div>
                            <span className={`text-sm font-medium ${isActive ? "text-[var(--primary)]" : "text-[var(--text-primary)]"}`}>
                              {t.name}
                            </span>
                            {t.is_builtin && (
                              <span className="ml-1.5 rounded bg-[var(--surface-hover)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
                                기본
                              </span>
                            )}
                            <p className="text-xs text-[var(--text-muted)] mt-0.5 truncate max-w-[200px]">{t.description}</p>
                          </div>
                          <span className="rounded-full bg-[var(--surface-hover)] px-2 py-0.5 text-xs font-medium text-[var(--text-secondary)]">
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
                      <div className="rounded-md bg-[var(--primary-light)] px-3 py-1.5 text-xs font-medium text-[var(--primary)]">
                        새 정책 생성
                      </div>
                    )}

                    {/* 정책명 */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                        정책명
                      </label>
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => { setEditName(e.target.value); setEditDirty(true); }}
                        disabled={!isCreatingNew && selectedTemplateObj?.is_builtin}
                        className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm disabled:bg-[var(--bg)] disabled:text-[var(--text-muted)]"
                        placeholder="예: standard, premium"
                      />
                    </div>

                    {/* 설명 */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                        설명
                      </label>
                      <input
                        type="text"
                        value={editDesc}
                        onChange={(e) => { setEditDesc(e.target.value); setEditDirty(true); }}
                        className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                        placeholder="정책 설명"
                      />
                    </div>

                    {/* 노드그룹 */}
                    <div>
                      <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                        노드그룹
                      </label>
                      <select
                        value={editNodegroup}
                        onChange={(e) => { setEditNodegroup(e.target.value); setEditDirty(true); }}
                        className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
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
                      <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                        노드당 Pod 수
                      </label>
                      <input
                        type="number"
                        min={1}
                        max={20}
                        value={editMaxPods}
                        onChange={(e) => { setEditMaxPods(Number(e.target.value)); setEditDirty(true); }}
                        className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                      />
                    </div>

                    {/* CPU / Memory grid */}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                          CPU Request
                        </label>
                        <input
                          type="text"
                          value={editCpuReq}
                          onChange={(e) => { setEditCpuReq(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                          placeholder="500m"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                          CPU Limit
                        </label>
                        <input
                          type="text"
                          value={editCpuLim}
                          onChange={(e) => { setEditCpuLim(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                          placeholder="1000m"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                          Mem Request
                        </label>
                        <input
                          type="text"
                          value={editMemReq}
                          onChange={(e) => { setEditMemReq(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                          placeholder="1.5Gi"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-1">
                          Mem Limit
                        </label>
                        <input
                          type="text"
                          value={editMemLim}
                          onChange={(e) => { setEditMemLim(e.target.value); setEditDirty(true); }}
                          className="w-full rounded-md border border-[var(--border-strong)] px-3 py-1.5 text-sm"
                          placeholder="3Gi"
                        />
                      </div>
                    </div>

                    {/* 공유 디렉토리 쓰기 */}
                    <div>
                      <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)] cursor-pointer">
                        <input
                          type="checkbox"
                          checked={editSharedWritable}
                          onChange={(e) => { setEditSharedWritable(e.target.checked); setEditDirty(true); }}
                          className="h-4 w-4 rounded border-[var(--border-strong)] text-[var(--primary)] focus:ring-[var(--primary)]"
                        />
                        공유 디렉토리 쓰기
                      </label>
                    </div>

                    {/* Action Buttons */}
                    <div className="flex gap-2 pt-2">
                      <button
                        onClick={handleSaveTemplate}
                        disabled={saving}
                        className="flex-1 rounded-md bg-[var(--primary)] px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-[var(--primary-hover)] disabled:opacity-50 transition-colors"
                      >
                        {saving ? "저장 중..." : isCreatingNew ? "생성" : "저장"}
                      </button>
                      {!isCreatingNew && selectedTemplateObj && !selectedTemplateObj.is_builtin && (
                        <button
                          onClick={handleDeleteTemplate}
                          className="rounded-md border border-[var(--danger)] px-3 py-2 text-sm font-medium text-[var(--danger)] hover:bg-[var(--danger-light)] transition-colors"
                        >
                          삭제
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {/* Empty state when nothing selected */}
                {!selectedTemplate && !isCreatingNew && (
                  <div className="px-4 py-8 text-center text-xs text-[var(--text-muted)]">
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
