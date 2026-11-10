# The features channel — cross-asset data for strategies

Strategies on the dMoERA contract can read more than their own symbol's
candles. Every bar carries a `ctx.features` dict, and the harness ships
per-bar feature values aligned 1:1 with the candle series.

## What's servable today

```text
Reference-asset returns (computed from public candles):
  {asset}_return_pct          e.g. btc_return_pct
  {asset}_return_{tag}_pct    lookback tags, e.g. btc_return_1h_pct

Binance metrics (open interest, funding, etc. per symbol):
  {metric}_{symbol}           e.g. open_interest_btcusdt, funding_ethusdt
```

`asset` maps to reference symbols (BTC, ETH, SOL, ...); metrics come from
Binance's public futures/metrics endpoints, cached per day.

## How it works

1. A strategy reads features explicitly:

```python
btc_ret = ctx.features.get("btc_return_1h_pct")
oi = ctx.features.get("open_interest_btcusdt")
```

2. The harness statically scans the strategy source for the exact keys it
   reads (`used_feature_keys`) and ships ONLY those keys per bar. A strategy
   that reads no features gets no features (same as production).

3. Fail-safe: if the scanner can't statically resolve the keys a strategy
   reads, the harness ships ALL servable features rather than risk silently
   starving a strategy of data it depends on.

## Bringing your own ML fields

The channel takes arbitrary per-bar dicts. If you have your own features
(sentiment scores, orderbook snapshots, your own model outputs), build a
`features_series` — a list of dicts, one per candle, aligned 1:1 — and any
strategy that reads your keys via `ctx.features.get(...)` will receive them.
The 7-stage screen runs identically over your features.

## Local parity

The pip package builds the identical feature series locally:

```python
pip install dmoera
```

```python
from dmoera.features import build_feature_series
feats = build_feature_series(candles, code, symbol="SOLUSDT")
```

`build_feature_series` mirrors production key resolution exactly (including
the fail-safe), pulls the same public data, and was parity-swept 10/10
against the production harness across 9 strategy families (2026-11-06).
Cross-asset strategies backtest identically locally and in production.
