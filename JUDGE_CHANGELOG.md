# Judge changelog

Every card in this repo is a verdict from a specific version of the validation
judge. When the judge changes, old cards don't get rewritten - they stay stamped
with the version that produced them (`judge_version` and `screened_at` on every
card). This file logs what changed and when, so a card from judge v1 can always
be interpreted against the current judge.

Direction matters: so far every change has only made the judge harsher. A bot
that died under v1 stays dead under v2+. A bot that passed v1 may die earlier
under v2+.

## v1 - the judge that screened this corpus

- Screened: Oct 9-13, 2026 (original 416 re-screened Oct 9-10, hand batches
  v2-v6b screened Oct 13)
- 7 stages on ~6 months of 5m Binance data: code safety, in-sample,
  out-of-sample, rolling walk-forward, randomized starts, slippage stress,
  holdout
- `ctx.bars` exposed the full window history to strategies on every bar
- `flat` signals were dropped by some simulation paths (treated as hold)

## v2 - 2026-10-21 (harsher)

- `ctx.bars` is now capped at 1000 bars, matching the `MAX_BARS` window live
  bots actually receive in production. Before this, a strategy could read the
  entire 52,800-bar window during backtest but would only ever see 1000 bars
  live, so backtest and live behavior could diverge silently.
- Impact: strategies that look back further than 1000 bars (some aggregate
  daily bars from 5m history, needing 7,000+ bars of context) can no longer
  produce their first signal under the cap. 23 bots in this repo carry a
  `bar_cap_note` on their card for exactly this reason.

## v3 - 2026-11-01 (harsher)

- `flat` signals now close any open position, matching the live adapter.
  Before this fix some simulation paths ignored `flat` and held the position,
  so long/flat strategies backtested with exits they don't have live.
- Impact: strategies that emit `flat` see different (usually earlier) exits,
  which shifts their trade paths. Roughly 1,400 strategies across the full
  corpus emit `flat`; a parity spot-check of 5 random bots found the
  flat-emitters dying earlier under v3, exactly as expected.
