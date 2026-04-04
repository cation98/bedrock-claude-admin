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
  phone_number: string | null;
  region_name: string | null;
  team_name: string | null;
  job_name: string | null;
  role: string;
  is_approved: boolean;
  pod_ttl: string;
  can_deploy_apps: boolean;
  is_presenter: boolean;
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

export function updateUserPhone(userId: number, phoneNumber: string): Promise<User> {
  return request<User>(`/api/v1/users/${userId}/phone`, {
    method: "PATCH",
    body: JSON.stringify({ phone_number: phoneNumber }),
  });
}

export function rejectUser(userId: number): Promise<{ deleted: boolean; username: string }> {
  return request<{ deleted: boolean; username: string }>(`/api/v1/users/${userId}`, {
    method: "DELETE",
  });
}

export function updateUserDeployApps(userId: number, canDeploy: boolean): Promise<User> {
  return request<User>(`/api/v1/users/${userId}/deploy-apps`, {
    method: "PATCH",
    body: JSON.stringify({ can_deploy_apps: canDeploy }),
  });
}

// ---------- Deployed Apps (Admin) ----------

export interface DeployedApp {
  id: number;
  owner_username: string;
  owner_name?: string;
  app_name: string;
  app_url: string;
  pod_name: string;
  status: string;
  version: string;
  visibility: string;
  app_port: number;
  acl_count?: number;
  view_count: number;
  unique_viewers: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface DeployedAppsResponse {
  apps: DeployedApp[];
}

export function getAdminApps(): Promise<DeployedAppsResponse> {
  return request<DeployedAppsResponse>("/api/v1/admin/apps");
}

export function getGalleryApps(): Promise<DeployedAppsResponse> {
  return request<DeployedAppsResponse>("/api/v1/apps/gallery");
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

export function addMemberDirectly(username: string, podTtl: string, phoneNumber?: string): Promise<User> {
  return request<User>("/api/v1/users/add-member", {
    method: "POST",
    body: JSON.stringify({ username, pod_ttl: podTtl, phone_number: phoneNumber || null }),
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

export interface UserUsageHistory {
  date: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_krw: number;
  session_minutes: number;
}

export interface UserUsageHistoryResponse {
  username: string;
  days: number;
  history: UserUsageHistory[];
}

export function getUserUsageHistory(username: string, from?: string, to?: string): Promise<UserUsageHistoryResponse> {
  const params = new URLSearchParams();
  if (from && to) {
    const d1 = new Date(from);
    const d2 = new Date(to);
    const diffDays = Math.ceil((d2.getTime() - d1.getTime()) / (1000 * 60 * 60 * 24)) + 1;
    params.set("days", String(Math.max(diffDays, 1)));
  }
  const q = params.toString() ? `?${params.toString()}` : "";
  return request<UserUsageHistoryResponse>(`/api/v1/admin/token-usage/user/${username}${q}`);
}

// ---------- Admin: Token Usage Daily Trend ----------

export interface DailyTrendItem {
  date: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_krw: number;
  active_users: number;
}

export interface DailyTrendResponse {
  days: number;
  trend: DailyTrendItem[];
}

export function getTokenUsageDailyTrend(days: number = 30): Promise<DailyTrendResponse> {
  return request<DailyTrendResponse>(`/api/v1/admin/token-usage/daily-trend?days=${days}`);
}

export interface MonthlyTrendItem {
  month: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_krw: number;
  active_users: number;
}

export interface MonthlyTrendResponse {
  from_month: string;
  trend: MonthlyTrendItem[];
}

export function getTokenUsageMonthlyTrend(fromMonth: string = "2026-03"): Promise<MonthlyTrendResponse> {
  return request<MonthlyTrendResponse>(`/api/v1/admin/token-usage/monthly-trend?from_month=${fromMonth}`);
}

// ---------- Admin: Token Quota Policy ----------

export interface QuotaTemplate {
  id: number;
  name: string;
  description: string;
  cost_limit_usd: number;
  refresh_cycle: string; // "daily" | "weekly" | "monthly"
  is_unlimited: boolean;
  created_at: string;
  updated_at: string;
}

export interface QuotaAssignment {
  user_id: number;
  username: string;
  name: string | null;
  template_name: string;
  cost_limit_usd: number;
  refresh_cycle: string;
  assigned_at: string;
}

export interface QuotaCheckResult {
  username: string;
  template_name: string;
  cost_limit_usd: number;
  current_usage_usd: number;
  remaining_usd: number;
  is_exceeded: boolean;
  is_unlimited: boolean;
  refresh_cycle: string;
  cycle_start: string;
  cycle_end: string;
}

export function getQuotaTemplates(): Promise<{ templates: QuotaTemplate[] }> {
  return request<{ templates: QuotaTemplate[] }>("/api/v1/admin/token-quota/templates");
}

export function createQuotaTemplate(data: Partial<QuotaTemplate>): Promise<QuotaTemplate> {
  return request<QuotaTemplate>("/api/v1/admin/token-quota/templates", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateQuotaTemplate(id: number, data: Partial<QuotaTemplate>): Promise<QuotaTemplate> {
  return request<QuotaTemplate>(`/api/v1/admin/token-quota/templates/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteQuotaTemplate(id: number): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/api/v1/admin/token-quota/templates/${id}`, {
    method: "DELETE",
  });
}

export function getQuotaAssignments(): Promise<{ assignments: QuotaAssignment[] }> {
  return request<{ assignments: QuotaAssignment[] }>("/api/v1/admin/token-quota/assignments");
}

export function assignQuota(data: { usernames: string[]; template_name: string }): Promise<{ assigned: number }> {
  return request<{ assigned: number }>("/api/v1/admin/token-quota/assign", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function checkQuota(username: string): Promise<QuotaCheckResult> {
  return request<QuotaCheckResult>(`/api/v1/admin/token-quota/check/${username}`);
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

// ---------- Token Usage Daily/Monthly ----------

export interface DailyUsageUser {
  username: string;
  user_name: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_krw: number;
  session_minutes: number;
  last_activity_at: string | null;
}

export interface DailyUsageResponse {
  date: string;
  users: DailyUsageUser[];
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cost_usd: number;
  total_cost_krw: number;
}

export interface MonthlyUsageResponse {
  month: string;
  users: DailyUsageUser[];
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cost_usd: number;
  total_cost_krw: number;
}

export function getTokenUsageDaily(date?: string): Promise<DailyUsageResponse> {
  const q = date ? `?date=${date}` : "";
  return request<DailyUsageResponse>(`/api/v1/admin/token-usage/daily${q}`);
}

export function getTokenUsageMonthly(month?: string): Promise<MonthlyUsageResponse> {
  const q = month ? `?month=${month}` : "";
  return request<MonthlyUsageResponse>(`/api/v1/admin/token-usage/monthly${q}`);
}

export function takeTokenSnapshot(): Promise<{ saved: number; date: string }> {
  return request<{ saved: number; date: string }>("/api/v1/admin/token-usage/snapshot", { method: "POST" });
}

export interface HourlyUsageResponse {
  date: string;
  users: Record<string, number[]>; // username -> 24 hourly values
}

export function getTokenUsageHourly(date?: string): Promise<HourlyUsageResponse> {
  const params = date ? `?date=${date}` : "";
  return request<HourlyUsageResponse>(`/api/v1/admin/token-usage/hourly${params}`);
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

// ---------- Security Policies ----------

export type SecurityLevel = "basic" | "standard" | "full" | (string & {});

export interface SecurityPolicyWithUser {
  user_id: number;
  username: string;
  name: string | null;
  region_name: string | null;
  team_name: string | null;
  role: string;
  security_level: SecurityLevel;
  security_policy: Record<string, unknown>;
  pod_restart_required: boolean;
}

export function getSecurityPolicies(): Promise<{ total: number; policies: SecurityPolicyWithUser[] }> {
  return request<{ total: number; policies: SecurityPolicyWithUser[] }>("/api/v1/security/policies");
}

export function updateSecurityPolicy(userId: number, data: Record<string, unknown>): Promise<SecurityPolicyWithUser> {
  return request<SecurityPolicyWithUser>(`/api/v1/security/policies/${userId}`, {
    method: "PUT",
    body: JSON.stringify({ security_policy: data }),
  });
}

export function applySecurityTemplate(userId: number, templateName: SecurityLevel): Promise<SecurityPolicyWithUser> {
  return request<SecurityPolicyWithUser>(`/api/v1/security/templates/apply/${userId}`, {
    method: "POST",
    body: JSON.stringify({ template_name: templateName }),
  });
}

export function getSecurityTemplates(): Promise<{ templates: { name: string; description: string; security_policy: Record<string, unknown> }[] }> {
  return request<{ templates: { name: string; description: string; security_policy: Record<string, unknown> }[] }>("/api/v1/security/templates");
}

// ---------- Security: Custom Templates ----------

export interface CustomTemplate {
  id: number;
  name: string;
  description: string;
  policy: Record<string, unknown>;
  created_by: string;
}

export function getCustomTemplates(): Promise<{ templates: CustomTemplate[] }> {
  return request<{ templates: CustomTemplate[] }>("/api/v1/security/custom-templates");
}

export function createCustomTemplate(data: { name: string; description: string; policy: Record<string, unknown> }): Promise<CustomTemplate> {
  return request<CustomTemplate>("/api/v1/security/custom-templates", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateCustomTemplate(id: number, data: Record<string, unknown>): Promise<CustomTemplate> {
  return request<CustomTemplate>(`/api/v1/security/custom-templates/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteCustomTemplate(id: number): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/api/v1/security/custom-templates/${id}`, {
    method: "DELETE",
  });
}

// ---------- Scheduling + Extension ----------

export interface ExtensionRequest {
  id: number;
  username: string;
  user_name: string | null;
  requested_hours: number;
  status: string;  // pending, approved, rejected
  requested_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

export function getExtensionRequests(): Promise<{ requests: ExtensionRequest[] }> {
  return request<{ requests: ExtensionRequest[] }>("/api/v1/schedule/extensions");
}

export function approveExtension(requestId: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/v1/schedule/extension/approve/${requestId}`, { method: "POST" });
}

export function rejectExtension(requestId: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/v1/schedule/extension/reject/${requestId}`, { method: "POST" });
}

// [비활성] 업무시간 스케줄 폐지 (2026-04-01) — Pod 수명은 사용자별 TTL로 관리
// export function triggerShutdownWarning(...)
// export function triggerShutdown(...)
// export function triggerStartup(...)

// ---------- Infra Policy ----------

export interface InfraTemplatePolicy {
  nodegroup: string;
  node_selector: Record<string, string> | null;
  max_pods_per_node: number;
  cpu_request: string;
  cpu_limit: string;
  memory_request: string;
  memory_limit: string;
  shared_dir_writable: boolean;
}

export interface InfraTemplateItem {
  id?: number;
  name: string;
  description: string;
  policy: InfraTemplatePolicy;
  is_builtin: boolean;
}

export interface InfraAssignment {
  user_id: number;
  username: string;
  name: string | null;
  infra_policy_name: string;
  infra_policy: Record<string, unknown>;
}

export function getInfraTemplates(): Promise<{ templates: InfraTemplateItem[] }> {
  return request<{ templates: InfraTemplateItem[] }>("/api/v1/infra-policy/templates");
}

export function createInfraTemplate(data: { name: string; description: string; policy: InfraTemplatePolicy }): Promise<InfraTemplateItem> {
  return request<InfraTemplateItem>("/api/v1/infra-policy/templates", {
    method: "POST", body: JSON.stringify(data),
  });
}

export function updateInfraTemplate(id: number, data: Record<string, unknown>): Promise<InfraTemplateItem> {
  return request<InfraTemplateItem>(`/api/v1/infra-policy/templates/${id}`, {
    method: "PUT", body: JSON.stringify(data),
  });
}

export function deleteInfraTemplate(id: number): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/api/v1/infra-policy/templates/${id}`, {
    method: "DELETE",
  });
}

export function getInfraAssignments(): Promise<{ assignments: InfraAssignment[] }> {
  return request<{ assignments: InfraAssignment[] }>("/api/v1/infra-policy/assignments");
}

export function assignInfraPolicy(data: { usernames: string[]; template_name: string }): Promise<{ assigned: number }> {
  return request<{ assigned: number }>("/api/v1/infra-policy/assign", {
    method: "POST", body: JSON.stringify(data),
  });
}

// ---------- Security: Table Info ----------

export interface TableInfo {
  name: string;
  description: string;
}

export function getSecurityTables(): Promise<{ safety: TableInfo[]; tango: TableInfo[]; doculog: TableInfo[] }> {
  return request<{ safety: TableInfo[]; tango: TableInfo[]; doculog: TableInfo[] }>("/api/v1/security/tables");
}

// ---------- Prompt Audit ----------

export interface PromptAuditUser {
  username: string;
  user_name: string | null;
  total_prompts: number;
  total_chars: number;
  category_counts: Record<string, number>;
  flagged_count: number;
}

export interface PromptAuditSummaryResponse {
  date_from: string;
  date_to: string;
  users: PromptAuditUser[];
  category_totals: Record<string, number>;
  total_prompts: number;
  total_flags: number;
}

export interface PromptAuditFlag {
  id: number;
  username: string;
  flagged_at: string;
  category: string;
  severity: string;
  prompt_excerpt: string;
  reason: string;
  reviewed: boolean;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface PromptAuditFlagsResponse {
  flags: PromptAuditFlag[];
}

export function getPromptAuditSummary(dateFrom?: string, dateTo?: string): Promise<PromptAuditSummaryResponse> {
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const q = params.toString();
  return request<PromptAuditSummaryResponse>(`/api/v1/admin/prompt-audit/summary${q ? "?" + q : ""}`);
}

export function getPromptAuditFlags(severity?: string, reviewed?: boolean, limit?: number): Promise<PromptAuditFlagsResponse> {
  const params = new URLSearchParams();
  if (severity) params.set("severity", severity);
  if (reviewed !== undefined) params.set("reviewed", String(reviewed));
  if (limit) params.set("limit", String(limit));
  const q = params.toString();
  return request<PromptAuditFlagsResponse>(`/api/v1/admin/prompt-audit/flags${q ? "?" + q : ""}`);
}

export function reviewPromptFlag(flagId: number): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/v1/admin/prompt-audit/flags/${flagId}/review`, { method: "POST" });
}

export function triggerPromptAudit(): Promise<{ analyzed: number }> {
  return request<{ analyzed: number }>("/api/v1/admin/prompt-audit/collect", { method: "POST" });
}

// ---------- Surveys ----------

export interface SurveyQuestion {
  type: "text" | "photo" | "choice";
  label: string;
  required: boolean;
  options?: string[];
}

export interface Survey {
  id: number;
  owner_username: string;
  title: string;
  description: string;
  questions: SurveyQuestion[];
  status: string;
  created_at: string;
  response_count: number;
}

export interface SurveyResponse {
  responder_username: string;
  answers: Record<string, unknown>;
  completed_at: string;
}

export function getSurveys(): Promise<Survey[]> {
  return request<Survey[]>("/api/v1/surveys");
}

export function createSurvey(data: {
  title: string;
  description: string;
  questions: SurveyQuestion[];
}): Promise<Survey> {
  return request<Survey>("/api/v1/surveys", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function assignSurvey(
  surveyId: number,
  targetUsernames: string[]
): Promise<unknown[]> {
  return request<unknown[]>(`/api/v1/surveys/${surveyId}/assign`, {
    method: "POST",
    body: JSON.stringify({ target_usernames: targetUsernames }),
  });
}

export function getSurveyResponses(surveyId: number): Promise<SurveyResponse[]> {
  return request<SurveyResponse[]>(`/api/v1/surveys/${surveyId}/responses`);
}

export function getPhotoUrl(s3Key: string): Promise<{ url: string }> {
  return request<{ url: string }>(
    `/api/v1/surveys/photo-url?s3_key=${encodeURIComponent(s3Key)}`
  );
}

// ---------- Network: Allowed Domains ----------

export interface AllowedDomain {
  id: number;
  domain: string;
  is_wildcard: boolean;
  description: string | null;
  enabled: boolean;
  created_by: string | null;
  created_at: string | null;
}

export function getAllowedDomains(): Promise<{ domains: AllowedDomain[] }> {
  return request<{ domains: AllowedDomain[] }>("/api/v1/admin/allowed-domains");
}

export function addAllowedDomain(data: { domain: string; description?: string; is_wildcard?: boolean }): Promise<AllowedDomain> {
  return request<AllowedDomain>("/api/v1/admin/allowed-domains", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateAllowedDomain(id: number, data: { enabled?: boolean; description?: string }): Promise<AllowedDomain> {
  return request<AllowedDomain>(`/api/v1/admin/allowed-domains/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteAllowedDomain(id: number): Promise<{ deleted: boolean; domain: string }> {
  return request<{ deleted: boolean; domain: string }>(`/api/v1/admin/allowed-domains/${id}`, {
    method: "DELETE",
  });
}

// ---------- Network: Proxy Logs ----------

export interface ProxyAccessLog {
  id: number;
  user_id: string | null;
  domain: string | null;
  method: string | null;
  allowed: boolean | null;
  response_time_ms: number | null;
  created_at: string | null;
}

export interface ProxyLogsResponse {
  total: number;
  skip: number;
  limit: number;
  logs: ProxyAccessLog[];
}

export function getProxyLogs(params?: { skip?: number; limit?: number; user_id?: string; domain?: string }): Promise<ProxyLogsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params?.limit !== undefined) searchParams.set("limit", String(params.limit));
  if (params?.user_id) searchParams.set("user_id", params.user_id);
  if (params?.domain) searchParams.set("domain", params.domain);
  const q = searchParams.toString();
  return request<ProxyLogsResponse>(`/api/v1/admin/proxy-logs${q ? "?" + q : ""}`);
}
