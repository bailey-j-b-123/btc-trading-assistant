# Dashboard paper-result scope

The initial full dashboard request and its read-only forward report request use
one saved symbol/timeframe selection. Previously only the dashboard request
passed this selection; the paper report silently used server defaults. For a
saved 5m preference and a 1h server default, initial paper counts could therefore
belong to 1h while the dashboard decision belonged to 5m.

Both requests now pass preferredSymbol/preferredTimeframe, omitting empty values
so existing server defaults still apply. The same render path serves full refresh.
The independent tracker poll already uses the returned dashboard metadata.

Chart timeframe buttons remain view-only: switching the displayed chart does not
change the decision/report timeframe. No qualification, planning, outcome,
occupancy, threshold, record identity or persistence policy changes.

The frontend regression supplies non-default saved preferences and asserts the
actual dashboard and forward request query scopes. This is an offline request
integration check, not a live-runtime or profitability test. The known
history-start invariance failure remains unresolved.
