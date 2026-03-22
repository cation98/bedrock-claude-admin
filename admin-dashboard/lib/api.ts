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
  pod_name: string;
  pod_status: string;
  session_type: string;
  started_at: string;
  terminal_url: string | null;
  files_url: string | null;
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
