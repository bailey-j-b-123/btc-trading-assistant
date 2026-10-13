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

/**
 * Client-side limit for the dashboard decision request. The server computes the decision on each
 * request, which can take tens of seconds on a large database; past this limit the page reports a
 * timeout with a retry instead of an unexplained failure. The server is not stopped and no stored
 * data is changed.
 */
export const DASHBOARD_TIMEOUT_MS = 180000;

/**
 * Fetch JSON with an optional time limit. Failures are typed so the page can say what happened:
 * ``timeout`` (no response within the limit), ``network_error`` (no response at all, e.g. the
 * connection was closed) or the server's own error code.
 */
export async function fetchJson(method, path, body, { timeoutMs = 0, fetchImpl = globalThis.fetch } = {}) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let timer = null;
  let timedOut = false;
  if (timeoutMs > 0) {
    const controller = new AbortController();
    options.signal = controller.signal;
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }
  let response;
  try {
    response = await fetchImpl(path, options);
  } catch (error) {
    if (timedOut) throw new ApiError(0, "timeout", `No response within ${Math.round(timeoutMs / 1000)} seconds.`);
    throw new ApiError(0, "network_error", error?.message || "The request did not complete.");
  } finally {
    if (timer) clearTimeout(timer);
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  return responseOrThrow(response, payload);
}

/** Map a parsed response to its payload, or to the typed ApiError the server's error body describes. */
function responseOrThrow(response, payload) {
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

/**
 * Operational deadline for the scoped stored-forward poll. It bounds the WHOLE read: the response
 * headers AND the JSON body. It is a client-side limit only: the server is not stopped and no
 * stored data is changed. It is shorter than the 15 s poll cadence, so a stalled read is released
 * and reported as unavailable before the next tick.
 */
export const FORWARD_READ_DEADLINE_MS = 10000;

/**
 * Fetch JSON and give up after ``deadlineMs`` covering the response headers AND the body.
 *
 * Unlike ``fetchJson`` (whose timer ends at the headers), the deadline here spans ``response.json()``
 * too. At the deadline the request's AbortController is aborted, so a real fetch releases its
 * connection and body, and the call rejects with ``ApiError`` code ``timeout``. A read that finishes
 * after the deadline is discarded: it can never resolve this call again. The shared ``Promise.race``
 * is the release path for a read that ignores its abort signal.
 */
export async function fetchJsonWithin(method, path, body, { deadlineMs, fetchImpl = globalThis.fetch } = {}) {
  if (!(deadlineMs > 0)) throw new Error("fetchJsonWithin requires a positive deadlineMs");
  const controller = new AbortController();
  const options = { method, headers: { Accept: "application/json" }, signal: controller.signal };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let timer = null;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new ApiError(0, "timeout", `No complete response within ${Math.round(deadlineMs / 1000)} seconds.`));
    }, deadlineMs);
  });
  const read = (async () => {
    let response;
    try {
      response = await fetchImpl(path, options);
    } catch (error) {
      throw new ApiError(0, "network_error", error?.message || "The request did not complete.");
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    return { response, payload };
  })();
  // A read abandoned at the deadline may reject later (its abort); that must not become an unhandled rejection.
  read.catch(() => {});
  deadline.catch(() => {});
  try {
    const { response, payload } = await Promise.race([read, deadline]);
    return responseOrThrow(response, payload);
  } finally {
    clearTimeout(timer);
  }
}

function request(method, path, body, options) {
  return fetchJson(method, path, body, options);
}

/**
 * Plain-language description of a failed request, with whether a retry can help. Used instead of a
 * generic "Something went wrong" so the reader knows whether to wait, retry or check the server.
 */
export function describeRequestFailure(error) {
  if (error instanceof ApiError && error.code === "timeout") {
    return {
      title: "The decision is still being calculated",
      detail:
        "The server did not return the dashboard within the time limit. Stored data is unchanged. Retry; if it repeats, the database is large and the calculation is slow.",
      retryable: true,
    };
  }
  if (error instanceof ApiError && error.code === "network_error") {
    return {
      title: "The dashboard server could not be reached",
      detail:
        "The browser got no response, so the connection was closed or the server is not running. Check that the dashboard server is running, then retry.",
      retryable: true,
    };
  }
  if (error instanceof ApiError && error.status >= 500) {
    return {
      title: "The dashboard server reported an error",
      detail: `HTTP ${error.status}${error.code ? ` (${error.code})` : ""}. ${error.message}`,
      retryable: true,
    };
  }
  if (error instanceof ApiError) {
    return { title: "The dashboard request was rejected", detail: error.message, retryable: false };
  }
  return {
    title: "The dashboard could not be loaded",
    detail: error?.message || "The backend returned an unexpected error.",
    retryable: true,
  };
}

export const api = {
  meta: () => request("GET", "/api/meta"),
  settings: () => request("GET", "/api/settings"),
  dashboard: (params = {}) =>
    fetchJsonWithin("GET", `/api/dashboard${queryString(params)}`, undefined, { deadlineMs: DASHBOARD_TIMEOUT_MS }),
  candles: (params = {}) => request("GET", `/api/market/candles${queryString(params)}`),
  /** Cheap boundary check for automatic refresh: newest stored closed candle, no analysis. */
  latestClosed: (params = {}) => request("GET", `/api/market/latest-closed${queryString(params)}`),
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
  forward: (params = {}) =>
    fetchJsonWithin("GET", `/api/forward${queryString(params)}`, undefined, { deadlineMs: DASHBOARD_TIMEOUT_MS }),
  // Bounded scoped poll of the same report (see fetchJsonWithin): the whole read, headers and body, has a deadline.
  forwardPoll: (params = {}) =>
    fetchJsonWithin("GET", `/api/forward${queryString(params)}`, undefined, { deadlineMs: FORWARD_READ_DEADLINE_MS }),
  // Lightweight read-only tracker poll: the ledger's paper_tracker block only,
  // so the dashboard card can pick up new/updated outcome versions without
  // re-requesting the full forward report or the dashboard analysis.
  forwardTracker: (params = {}) => request("GET", `/api/forward/paper-tracker${queryString(params)}`),
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
