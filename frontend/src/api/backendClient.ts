const API_BASE = ((import.meta.env.VITE_API_BASE as string | undefined) || '').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const TOKEN_KEY = 'pci_session_token';

/** Opaque session token issued by the backend at sign-in; the password itself is never stored in the browser. */
export const sessionStore = {
  get(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable: the session lasts until the page is closed */
    }
  },
  clear(): void {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  },
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (init?.body) headers['Content-Type'] = 'application/json';
  const token = sessionStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, 'network', 'Cannot reach the backend. Start it with start.ps1 (port 8000).');
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, body?.error?.code ?? 'http_error', body?.error?.message ?? res.statusText);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown) =>
  request<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) });

export type RunStatus = 'queued' | 'running' | 'reviewing' | 'completed' | 'partial' | 'failed' | 'interrupted';
export type RunMode = 'live_model' | 'deterministic_demo';
export type TaskStatus = 'open' | 'in_progress' | 'verified' | 'dismissed';
export type Priority = 'low' | 'medium' | 'high';
export const ACTIVE_RUN_STATUSES: RunStatus[] = ['queued', 'running', 'reviewing'];

export interface Coverage {
  row_count: number;
  observed_min_date: string | null;
  observed_max_date: string | null;
  fetched_at: string | null;
  origin: 'live_api' | 'snapshot_file' | null;
  complete: boolean;
  cache_status: 'cached' | 'cached_after_failed_refresh' | 'no_data';
  refresh_due: boolean;
  label: string;
  last_error: string | null;
  last_attempt_at: string | null;
  resource_totals: Record<string, { total: number; retrieved: number; complete: boolean }>;
  dataset_url: string;
  api_query_url: string;
  historical_note: string;
}

export interface PropertySummary {
  id: number;
  hcad: string;
  address: string;
  zip: string | null;
  is_demo: boolean;
  case_count: number;
  open_task_count: number;
  coverage: Coverage;
  latest_run: { id: number; status: RunStatus; mode: RunMode } | null;
  active_run_id: number | null;
}

export interface SourceRecord {
  evidence_id: number;
  resource_id: string;
  resource_label: string;
  source_row_id: string;
  case_id: string | null;
  violation_id: string | null;
  fetched_at: string;
  fields: Record<string, string | null>;
  raw: Record<string, unknown>;
  source_url: string;
}

export interface CaseGroup {
  case_id: string | null;
  created_date: string | null;
  latest_record_date: string | null;
  source_statuses: string[];
  categories: string[];
  violation_row_count: number;
  project_row_count: number;
  service_request_ids: string[];
  evidence_ids: number[];
  records: SourceRecord[];
  is_open: boolean;
  plan: CasePlan | null;
}

export interface PlanStep {
  id: number;
  position: number;
  title: string;
  detail: string;
  ordinance: string | null;
  evidence_ids: number[];
  done: boolean;
  done_at: string | null;
}

export interface CasePlan {
  id: number;
  property_id: number;
  case_id: string;
  status: string;
  mode: RunMode;
  model: string | null;
  summary: string;
  source_status: string | null;
  evidence_ids: number[];
  created_at: string;
  updated_at: string;
  progress: { done: number; total: number };
  steps: PlanStep[];
}

export interface CasesResponse {
  property_id: number;
  coverage: Coverage;
  cases: CaseGroup[];
  category_recurrence: { category: string; distinct_cases: number; case_ids: string[] }[];
}

export interface FeedbackItem { id: number; action: string; note: string | null; created_at: string }

export interface Task {
  id: number;
  property_id: number;
  task_key: string;
  action_type: string;
  case_ids: string[];
  title: string;
  reason: string;
  status: TaskStatus;
  priority: Priority;
  evidence_ids: number[];
  last_run_id: number | null;
  created_at: string;
  updated_at: string;
  feedback: FeedbackItem[];
}

export interface Finding {
  id: number;
  proposal_id: string;
  run_id: number;
  type: string;
  summary: string;
  evidence_ids: number[];
  uncertainty: string;
  review_status: string;
  issues: string[];
}

export interface Proposal {
  id: number;
  proposal_id: string;
  run_id: number;
  task_key: string;
  action_type: string;
  case_ids: string[];
  title: string;
  reason: string;
  priority: Priority;
  evidence_ids: number[];
  status: string;
  issues: string[];
  task_id: number | null;
}

export interface RunEvent { id: number; type: string; summary: string; created_at: string }

export interface Investigation {
  id: number;
  property_id: number;
  property: { id: number; hcad: string; address: string };
  status: RunStatus;
  mode: RunMode;
  model: string | null;
  summary: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  events: RunEvent[];
  findings: Finding[];
  proposals: Proposal[];
  tasks: Task[];
  coverage: Coverage;
  budget: {
    tool_calls_used: number;
    max_tool_calls: number;
    revision_calls_used: number;
    elapsed_s: number;
    time_budget_s: number;
    phase: string | null;
    stop_reason: string | null;
  };
}

export interface RunListItem {
  id: number;
  property_id: number;
  status: RunStatus;
  mode: RunMode;
  model: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Health {
  status: string;
  database: string;
  worker_alive: boolean;
  model: {
    mode: RunMode; provider: string; model: string | null; endpoint_host: string | null; tool_mode: string;
    warning: string | null;
  };
  accounts?: { configured: boolean };
  data: { source: string; dataset_url: string; imported_properties: number };
}

export interface AccountUser {
  id: string;
  email: string;
  full_name: string | null;
  company: string | null;
}

export interface SavedPropertyRow {
  id: string;
  user_id: string;
  hcad: string;
  address: string;
  zip?: string | null;
  created_at: string;
}

export interface Candidate {
  hcad: string;
  addresses: string[];
  zips: string[];
  matched_rows: number;
  case_count_in_sample: number;
  imported_property_id: number | null;
  ambiguous: boolean;
  shares_address_with_other_parcel: boolean;
}

export interface CandidatesResponse {
  query: string;
  candidates: Candidate[];
  rows_without_hcad: number;
  note: string | null;
}

const q = (params: Record<string, string | number | undefined | null>) => {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
};

export const api = {
  health: () => request<Health>('/api/health'),
  properties: (query?: string) => request<PropertySummary[]>(`/api/properties${q({ query })}`),
  property: (id: number) => request<PropertySummary>(`/api/properties/${id}`),
  cases: (id: number) => request<CasesResponse>(`/api/properties/${id}/cases`),
  refresh: (id: number) =>
    post<{ status: string; message: string; property: PropertySummary }>(`/api/properties/${id}/refresh`),
  candidates: (query: string) => request<CandidatesResponse>(`/api/source/candidates${q({ query })}`),
  importProperty: (hcad: string) => post<PropertySummary>('/api/properties/import', { hcad }),
  evidence: (ids: number[]) => request<SourceRecord[]>(`/api/evidence${q({ ids: ids.join(',') })}`),
  startInvestigation: (propertyId: number) =>
    post<{ run_id: number; status: RunStatus }>('/api/investigations', { property_id: propertyId }),
  investigations: (propertyId: number) => request<RunListItem[]>(`/api/investigations${q({ property_id: propertyId })}`),
  investigation: (id: number) => request<Investigation>(`/api/investigations/${id}`),
  resume: (id: number) => post<{ run_id: number; status: RunStatus }>(`/api/investigations/${id}/resume`),
  tasks: (propertyId?: number) => request<Task[]>(`/api/tasks${q({ property_id: propertyId })}`),
  updateTask: (id: number, body: { status?: TaskStatus; note?: string }) =>
    request<Task>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  approveProposal: (id: number, note?: string) =>
    post<{ outcome: { outcome: string; issues?: string[] } }>(`/api/proposals/${id}/approve`, note ? { note } : {}),
  rejectProposal: (id: number, note?: string) => post(`/api/proposals/${id}/reject`, note ? { note } : {}),
  generatePlan: (propertyId: number, caseId: string, regenerate = false) =>
    post<CasePlan>(`/api/properties/${propertyId}/cases/${encodeURIComponent(caseId)}/plan`, { regenerate }),
  updateStep: (stepId: number, done: boolean) =>
    request<CasePlan>(`/api/plan-steps/${stepId}`, { method: 'PATCH', body: JSON.stringify({ done }) }),
  signUp: (body: { email: string; password: string; full_name?: string; company?: string }) =>
    post<{ token: string; user: AccountUser }>('/api/auth/signup', body),
  signIn: (email: string, password: string) =>
    post<{ token: string; user: AccountUser }>('/api/auth/signin', { email, password }),
  me: () => request<AccountUser>('/api/auth/me'),
  signOut: () => post<null>('/api/auth/signout'),
  portfolio: () => request<SavedPropertyRow[]>('/api/portfolio'),
  savePortfolio: (p: { hcad: string; address: string; zip?: string | null }) =>
    post<SavedPropertyRow>('/api/portfolio', { hcad: p.hcad, address: p.address, zip: p.zip ?? null }),
  removePortfolio: (hcad: string) => request<null>(`/api/portfolio/${encodeURIComponent(hcad)}`, { method: 'DELETE' }),
};
