# Bounded initial and full-refresh reads

The initial/full dashboard render awaits both dashboard analysis and the forward
report. An indefinitely pending forward request prevented that render completing
and prevented its independent polling from starting. The dashboard request's
older timeout also ended when response headers arrived, leaving a stalled JSON
body outside the limit.

Both API methods now reuse PR56's whole-response `fetchJsonWithin` helper, with
the existing 180-second `DASHBOARD_TIMEOUT_MS` operational load limit. The helper
aborts the request and rejects at the limit, including while reading JSON; a
late completion cannot replace the failure. The existing render handles a
failed forward read as unavailable while displaying available analysis and
starting the existing bounded forward polling. A failed analysis read keeps the
existing failure/last-good-page behavior. The 10-second poll deadline is
unchanged.

Request URLs, saved selection, dashboard composition, engine inputs, thresholds,
risk rules and outcomes are unchanged. No sizing or performance computation is
added. Large-ledger latency and sustained operation still need separate tests.

This change is stacked on PR56's exact head
`ee8fe2ce5ca6161e36c1c81ea088f7534b99fe3d` to reuse its helper without copying it.
It changes no PR53–57 branch and no Dashboard V2 visual file. Integration needs
PR56 first; PR54 continues to own the initial request's selection arguments.
