/** API client: session-cookie auth with double-submit CSRF. */

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)tab_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-CSRF-Token", csrfToken());
  }
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401 && !path.startsWith("/api/v1/auth/")) {
    window.dispatchEvent(new CustomEvent("tabledger:unauthorized"));
  }
  const type = response.headers.get("content-type") ?? "";
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const body = type.includes("application/json") ? await response.json() : null;
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new ApiError(response.status, detail);
  }
  if (type.includes("application/json")) return response.json();
  return response as unknown as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// ---------- types ----------

export interface SessionInfo {
  username: string;
  must_change_password: boolean;
}

export interface JobFile {
  id: number;
  name: string;
  state: string;
  source: string;
  password_ok: boolean;
  error: string;
}

export interface Job {
  id: string;
  title: string;
  status: string;
  error: string;
  created_at: string;
  stats: Record<string, number>;
  summary: Record<string, unknown>;
  files?: JobFile[];
}

export interface Transaction {
  record_uid: string;
  source: string;
  datetime: string;
  flow: string;
  amount: number;
  net_amount: number;
  disposition: string;
  disposition_reason: string;
  merchant: string;
  item: string;
  account: string;
  status: string;
  refund_original_uid: string | null;
  refund_uids: string[];
  matched_uids: string[];
  review_reasons: string[];
  category: string;
  subcategory: string;
  tags: string;
  basis: string;
  classification_state: string;
}

export interface UnmatchedKey {
  merchant: string;
  direction: string;
  count: number;
  amount: number;
  items: string[];
}

export interface MatchExplain {
  match_id: string;
  match_type: string;
  confidence: string;
  decision: string;
  reason: string;
  platform_uids: string[];
  bank_uids: string[];
  original_uids: string[];
  refund_uids: string[];
  account: string;
  amount_cents: number | null;
  time_delta_seconds: number | null;
}

export const SOURCE_LABEL: Record<string, string> = {
  wx: "微信",
  zfb: "支付宝",
  工商银行: "工商银行",
  中国银行: "中国银行",
};

export const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  extracting: "提取账单",
  reconciling: "对账去重",
  classifying: "自动分类",
  needs_review: "待审核",
  exporting: "导出中",
  done: "已完成",
  failed: "失败",
  interrupted: "已中断",
};
