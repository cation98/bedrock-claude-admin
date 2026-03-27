import { getToken, logout } from "./auth";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (res.status === 401) {
    logout();
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `HTTP ${res.status}`);
  }

  return res.json() as Promise<T>;
}

// ---------- Auth ----------

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  username: string;
  name: string;
  role: string;
}

export function login(data: LoginRequest): Promise<LoginResponse> {
  return request<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ---------- 2FA Auth ----------

export interface LoginStep1Response {
  requires_2fa: boolean;
  code_id: string;
  phone_masked: string;
  message: string;
}

export interface Verify2faRequest {
  code_id: string;
  code: string;
}

// Note: login() may return EITHER LoginResponse (bypass) OR LoginStep1Response (2FA needed)
// The caller checks for requires_2fa field

export function verify2fa(data: Verify2faRequest): Promise<LoginResponse> {
  return request<LoginResponse>("/api/v1/auth/verify-2fa", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export interface MeResponse {
  username: string;
  name: string;
  role: string;
}

export function getMe(): Promise<MeResponse> {
  return request<MeResponse>("/api/v1/auth/me");
}

// ---------- Sessions ----------

export interface Session {
  id: number;
  username: string;
  user_name: string | null;
  pod_name: string;
  pod_status: string;
  session_type: string;
  started_at: string;
  terminal_url: string | null;
  files_url: string | null;
  hub_url: string | null;
  expires_at: string | null;
}

export function createMySession(): Promise<Session> {
  return request<Session>("/api/v1/sessions/", {
    method: "POST",
    body: JSON.stringify({ session_type: "workshop" }),
  });
}

export interface ActiveSessionsResponse {
  total: number;
  sessions: Session[];
}

export function getActiveSessions(): Promise<ActiveSessionsResponse> {
  return request<ActiveSessionsResponse>("/api/v1/sessions/active");
}

export interface BulkCreateRequest {
  usernames: string[];
  session_type: string;
}

export interface BulkCreateResponse {
  total: number;
  sessions: Session[];
}

export function bulkCreateSessions(
  data: BulkCreateRequest
): Promise<BulkCreateResponse> {
  return request<BulkCreateResponse>("/api/v1/sessions/bulk", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export interface BulkDeleteResponse {
  terminated: number;
}

export function bulkDeleteSessions(): Promise<BulkDeleteResponse> {
  return request<BulkDeleteResponse>("/api/v1/sessions/bulk", {
    method: "DELETE",
  });
}

export function adminTerminateSession(sessionId: number): Promise<Session> {
  return request<Session>(`/api/v1/sessions/admin/${sessionId}`, {
    method: "DELETE",
  });
}

// ---------- Users ----------

export interface User {
  id: number;
  username: string;
  name: string | null;
  region_name: string | null;
  team_name: string | null;
  job_name: string | null;
  role: string;
  is_approved: boolean;
  pod_ttl: string;
  approved_at: string | null;
  last_login_at: string | null;
}

export interface UserListResponse {
  total: number;
  users: User[];
}

export function getUsers(): Promise<UserListResponse> {
  return request<UserListResponse>("/api/v1/users/");
}

export function getPendingUsers(): Promise<UserListResponse> {
  return request<UserListResponse>("/api/v1/users/pending");
}

export function approveUser(userId: number, podTtl: string): Promise<User> {
  return request<User>(`/api/v1/users/${userId}/approve`, {
    method: "PATCH",
    body: JSON.stringify({ pod_ttl: podTtl }),
  });
}

export function updateUserTtl(userId: number, podTtl: string): Promise<User> {
  return request<User>(`/api/v1/users/${userId}/ttl`, {
    method: "PATCH",
    body: JSON.stringify({ pod_ttl: podTtl }),
  });
}

export function revokeUser(userId: number): Promise<User> {
  return request<User>(`/api/v1/users/${userId}/approve`, {
    method: "DELETE",
  });
}

export function rejectUser(userId: number): Promise<{ deleted: boolean; username: string }> {
  return request<{ deleted: boolean; username: string }>(`/api/v1/users/${userId}`, {
    method: "DELETE",
  });
}

// ---------- Users: Search + Direct Add ----------

export interface OGuardProfile {
  username: string;
  first_name: string | null;
  region_name: string | null;
  team_name: string | null;
  job_name: string | null;
}

export interface OGuardSearchResponse {
  total: number;
  results: OGuardProfile[];
}

export function searchMembers(q: string): Promise<OGuardSearchResponse> {
  return request<OGuardSearchResponse>(`/api/v1/users/search-members?q=${encodeURIComponent(q)}`);
}

export function addMemberDirectly(username: string, podTtl: string): Promise<User> {
  return request<User>("/api/v1/users/add-member", {
    method: "POST",
    body: JSON.stringify({ username, pod_ttl: podTtl }),
  });
}

// ---------- Admin: Pod Management ----------

export interface PodActionResponse {
  username: string;
  pod_name: string;
  status: string;
  node_name: string | null;
}

export function assignPod(username: string, nodeName?: string): Promise<PodActionResponse> {
  return request<PodActionResponse>("/api/v1/admin/assign-pod", {
    method: "POST",
    body: JSON.stringify({ username, node_name: nodeName ?? null }),
  });
}

export function movePod(username: string, targetNode: string): Promise<PodActionResponse> {
  return request<PodActionResponse>("/api/v1/admin/move-pod", {
    method: "POST",
    body: JSON.stringify({ username, target_node: targetNode }),
  });
}

export function terminatePod(username: string): Promise<{ username: string; status: string }> {
  return request<{ username: string; status: string }>(`/api/v1/admin/terminate-pod/${username}`, {
    method: "DELETE",
  });
}

// ---------- Admin: Node Group Scaling ----------

export interface NodeGroupInfo {
  name: string;
  instance_type: string;
  min_size: number;
  max_size: number;
  desired_size: number;
  status: string;
}

export interface NodeGroupListResponse {
  groups: NodeGroupInfo[];
}

export function getNodeGroups(): Promise<NodeGroupListResponse> {
  return request<NodeGroupListResponse>("/api/v1/admin/nodegroups");
}

export function scaleNodeGroup(nodegroupName: string, desiredSize: number): Promise<{ nodegroup: string; desired_size: number; status: string }> {
  return request<{ nodegroup: string; desired_size: number; status: string }>("/api/v1/admin/scale-nodegroup", {
    method: "POST",
    body: JSON.stringify({ nodegroup_name: nodegroupName, desired_size: desiredSize }),
  });
}

// ---------- Admin: Node Drain ----------

export function drainNode(nodeName: string): Promise<{ node_name: string; status: string }> {
  return request<{ node_name: string; status: string }>("/api/v1/admin/drain-node", {
    method: "POST",
    body: JSON.stringify({ node_name: nodeName }),
  });
}

// ---------- Admin: Token Usage ----------

export interface UserTokenUsage {
  username: string;
  user_name: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_krw: number;
}

export interface TokenUsageResponse {
  users: UserTokenUsage[];
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cost_usd: number;
  total_cost_krw: number;
  collected_at: string;
}

export function getTokenUsage(): Promise<TokenUsageResponse> {
  return request<TokenUsageResponse>("/api/v1/admin/token-usage");
}

// ---------- Admin: Infrastructure ----------

export interface PodInfo {
  pod_name: string;
  username: string;
  user_name: string | null;
  status: string;
  node_name: string;
  cpu_request: string;
  memory_request: string;
  created_at: string | null;
}

export interface NodeInfo {
  node_name: string;
  instance_type: string;
  status: string;
  cpu_capacity: string;
  memory_capacity: string;
  node_role: string;
  pods: PodInfo[];
}

export interface InfraResponse {
  nodes: NodeInfo[];
  total_nodes: number;
  total_pods: number;
  collected_at: string;
}

export function getInfrastructure(): Promise<InfraResponse> {
  return request<InfraResponse>("/api/v1/admin/infrastructure");
}
