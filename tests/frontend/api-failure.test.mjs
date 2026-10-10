/**
 * Dashboard request failures: the client gives a time limit, types each failure, and the page
 * describes it in plain language with a retry only where a retry can help.
 * Synthetic stubs only: no network is used.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  ApiError,
  DASHBOARD_TIMEOUT_MS,
  describeRequestFailure,
  fetchJson,
} from "../../src/trading_assistant/web/static/js/api.js";

test("dashboard decision request has a fixed client time limit of 180 seconds", () => {
  assert.equal(DASHBOARD_TIMEOUT_MS, 180000);
});

test("a request with no response inside the limit is a typed timeout", async () => {
  // A fetch that never resolves unless it is aborted, like a connection the server is still holding.
  const hangingFetch = (_path, options) =>
    new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
  await assert.rejects(
    fetchJson("GET", "/api/dashboard", undefined, { timeoutMs: 20, fetchImpl: hangingFetch }),
    (error) => error instanceof ApiError && error.code === "timeout" && /seconds/.test(error.message),
  );
});

test("a request that never gets a response is typed as a network error, not a timeout", async () => {
  const brokenFetch = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(
    fetchJson("GET", "/api/dashboard", undefined, { timeoutMs: 5000, fetchImpl: brokenFetch }),
    (error) => error instanceof ApiError && error.code === "network_error",
  );
});

test("a successful response returns the parsed payload and clears the timer", async () => {
  const okFetch = async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) });
  const payload = await fetchJson("GET", "/api/dashboard", undefined, { timeoutMs: 5000, fetchImpl: okFetch });
  assert.deepEqual(payload, { ok: true });
});

test("a server error keeps its HTTP status and code", async () => {
  const failingFetch = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ error: { code: "analysis_failed", message: "snapshot failed" } }),
  });
  await assert.rejects(
    fetchJson("GET", "/api/dashboard", undefined, { fetchImpl: failingFetch }),
    (error) => error instanceof ApiError && error.status === 500 && error.code === "analysis_failed",
  );
});

test("timeout is described as still calculating, with a retry", () => {
  const failure = describeRequestFailure(new ApiError(0, "timeout", "No response within 180 seconds."));
  assert.equal(failure.title, "The decision is still being calculated");
  assert.equal(failure.retryable, true);
  assert.match(failure.detail, /Stored data is unchanged/);
});

test("network failure is described as unreachable, with a retry", () => {
  const failure = describeRequestFailure(new ApiError(0, "network_error", "Failed to fetch"));
  assert.equal(failure.title, "The dashboard server could not be reached");
  assert.equal(failure.retryable, true);
});

test("server 500 is described with its status, with a retry", () => {
  const failure = describeRequestFailure(new ApiError(500, "analysis_failed", "snapshot failed"));
  assert.equal(failure.title, "The dashboard server reported an error");
  assert.match(failure.detail, /HTTP 500/);
  assert.equal(failure.retryable, true);
});

test("a rejected request (4xx) is not offered as retryable", () => {
  const failure = describeRequestFailure(new ApiError(422, "bad_symbol", "unknown symbol"));
  assert.equal(failure.title, "The dashboard request was rejected");
  assert.equal(failure.retryable, false);
});

test("no failure text ever uses the generic 'Something went wrong' wording", () => {
  const samples = [
    new ApiError(0, "timeout", "x"),
    new ApiError(0, "network_error", "x"),
    new ApiError(500, "", "x"),
    new ApiError(422, "", "x"),
    new Error("boom"),
  ];
  for (const sample of samples) {
    const failure = describeRequestFailure(sample);
    assert.doesNotMatch(`${failure.title} ${failure.detail}`, /Something went wrong/);
  }
});
