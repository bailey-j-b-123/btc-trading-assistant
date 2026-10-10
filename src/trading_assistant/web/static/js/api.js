/**
 * API client. All data flows through the application backend; the browser
 * never contacts an exchange or computes engine outputs itself.
 */

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message || `HTTP ${status}`);
    this.status = status;
    this.code = code;
  }
}

async function request(method, path, body) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.error ? payload.error : payload && payload.detail ? payload.detail : {};
    const code = typeof detail === "object" && detail.code ? detail.code : `http_${response.status}`;
    const message =
      typeof detail === "object" && detail.message
        ? detail.message
        : typeof detail === "string"
          ? detail
          : `Request failed with status ${response.status}`;
    throw new ApiError(response.status, code, message);
  }
  return payload;
}

export const api = {
  meta: () => request("GET", "/api/meta"),
  settings: () => request("GET", "/api/settings"),
  dashboard: (params = {}) => request("GET", `/api/dashboard${queryString(params)}`),
  candles: (params = {}) => request("GET", `/api/market/candles${queryString(params)}`),
  structure: (params = {}) => request("GET", `/api/market/structure${queryString(params)}`),
  annotations: (params = {}) => request("GET", `/api/market/annotations${queryString(params)}`),
  journalList: (params = {}) => request("GET", `/api/journal/records${queryString(params)}`),
  journalDetail: (journalId) => request("GET", `/api/journal/records/${encodeURIComponent(journalId)}`),
  recordDecision: (journalId, body) =>
    request("POST", `/api/journal/records/${encodeURIComponent(journalId)}/decisions`, body),
  observeOutcome: (journalId, body = {}) =>
    request("POST", `/api/journal/records/${encodeURIComponent(journalId)}/observations`, body),
  dashboardDecision: (body) => request("POST", "/api/dashboard/decisions", body),
  statistics: (params = {}) => request("GET", `/api/statistics${queryString(params)}`),
  rollingStatistics: (params = {}) => request("GET", `/api/statistics/rolling${queryString(params)}`),
  validation: (params = {}) => request("GET", `/api/validation${queryString(params)}`),
  forward: (params = {}) => request("GET", `/api/forward${queryString(params)}`),
  forwardComparison: (params = {}) =>
    request("GET", `/api/forward/comparison${queryString(params)}`),
};

export function queryString(params) {
  const entries = Object.entries(params).filter(
    ([, value]) => value !== undefined && value !== null && value !== "",
  );
  if (!entries.length) return "";
  const search = new URLSearchParams();
  for (const [key, value] of entries) search.set(key, String(value));
  return `?${search.toString()}`;
}
