# Crypto Strategies - 2,742 corpses + 10 old survivors that all failed the re-screen

[![Live on dMoERA](https://img.shields.io/badge/live-dMoERA.xyz-7c3aed)](https://www.dmoera.xyz/arena)
[![Validation](https://img.shields.io/badge/validation-7%20stage-orange)](https://www.dmoera.xyz/docs/user/validation-pipeline)
[![Listed on awesome-quant](https://img.shields.io/badge/listed%20on-awesome--quant%20%E4%B8%AD%E6%96%87%E7%89%88-8F3985)](https://github.com/thuquant/awesome-quant#%E7%AD%96%E7%95%A5)

I generated a big batch of crypto trading strategies and ran every single one through a strict 7-stage validation pipeline on real Binance data. This repo holds 2,742 of them: 2,742 that died under the current judge. The 10 in `passed/` cleared the ORIGINAL softer judge back in October - then the judge got stricter and the re-screen killed every one of them (0/10 re-passed). They stay in the repo as the most instructive corpses I have. Each one ships with its report card so you can see exactly what it did and where it stood.

Why publish this? Because everyone posts shiny backtests and almost nobody shows the failures. And the "passing" ones everyone copy trades. I figured a repo full of corpses with autopsy reports (including my old survivors, which died the moment the judge got honest) is more useful than another pile of "7 Sharpe", "profitable" scripts with no receipts.

## What's inside

```
strategies/   2,732 rejected strategy files (Python)
passed/       10 strategies that cleared the ORIGINAL judge - all 10
              failed the strict re-screen, kept for the autopsy value
cards/        one JSON report card per strategy - stage-by-stage status,
              out-of-sample returns, Sharpe, drawdown, failure class,
              and re-screen verdicts where they exist
index.json    all 2,742 entries, passed first, then sorted by OOS return
FINDINGS.md   what testing all of this actually taught me
```

## Drops

Corpses don't arrive all at once - every new batch gets screened, killed and published. Roughly one drop a week through November, ~300-400 bots at a time:

| Drop | Date | What landed |
|---|---|---|
| Original corpus | - | 416 strategies (406 rejects + 10 passed), the first 6-month screen |
| Drop 1 | Oct 31, 2026 | +332 hand-tuned batch corpses (v2-v6b, crypto-only) with full code and stage-by-stage autopsies, plus strict re-screen verdicts (`rescreens` block) on all 416 original cards |
| Drop 2 | Nov 10, 2026 | +1,994 corpses from a new batch of 2,000 screened strategies, split into three cohorts (`exploit` ~1,200 variations of stuff that already worked / `explore` 500 new ideas / `wildcard` 300 weird stuff). 6 admitted (0.3%) and stay private. Every card carries a `generation_bucket` field. Funnel: 1,197 died in-sample, 668 out-of-sample, 98 on robustness, 29 on holdout, 2 on static checks |

## The numbers

- 2,742 strategies in this repo. The 10 in `passed/` cleared the original pipeline - under the newer strict re-screen, 0 of them re-passed in strict01 and exactly 1 of the 416 re-passed in strict02 (a former reject). Each card now carries `passed_v1_judge` (the original verdict) and `current_verdict` (the latest re-screen), so old verdicts stay readable without looking broken.
- 234 of the rejects never even survived their own in-sample backtest.
- 170 made money in-sample then died out-of-sample.
- 25 were profitable out-of-sample and STILL got rejected because they broke under walk-forward and slippage stress. A green OOS number on its own means very little.
- Every validation ran on roughly six months of real 5-minute Binance candles, split so the strategy never sees its test windows.
- Admits within a batch are frequently parameter neighbors of one mechanism (e.g. 12 of the v6 batch's 18 admits are `v6_sol_confl` variants). Treat N neighbor admits as closer to one finding than N independent results. Cards carry `batch_size` and `batch_admits` so you can judge each verdict against its batch, and a `neighbor_note` where a single mechanism dominates a batch's admits.
- The Drop 2 batch adds its own finding: the cohort deliberately aimed at previously-productive mechanism families admitted at 0.33% - the same rate as the near-random cohort. Aiming didn't help; the screen did all the work.

The pattern that kept repeating: families built on context (session timing, fear-and-greed extremes, funding dislocations) generalized. Families built on candle geometry (Donchian breaks, Bollinger squeezes, RSI reversion, orderbook imbalance) memorized. Every BTC scalping variant failed - 0 for 18. At ~7bps a side in fees, the edge you need on 5-minute bars is just brutal. Full breakdown in [FINDINGS.md](FINDINGS.md).

## Running the bots locally

Every file is real runnable Python on the dMoERA strategy contract (`Strategy`, `BarContext`, `Signal`), not pseudocode. The backtest engine is now a pip package - the same engine our validation pipeline runs, deterministic (seeded, no network, no wall clock) with ~12bps round-trip friction:

```
pip install dmoera
dmoera backtest strategies/accel_mom_sol.py --symbol SOLUSDT --days 180
dmoera validate strategies/v6_sol_confl_85_130.py
dmoera battery strategies/weekend_season_eth_v22.py
```

`dmoera backtest` runs any file in this repo on public Binance data; `dmoera validate` runs the 7-stage screen; `dmoera battery` runs the 6-gate deep battery on 6.25 years of 1h bars (the same battery that killed 363 hand-built bots, 211 of them net-positive). You can also paste any file into a free paper account at [dmoera.xyz/studio](https://www.dmoera.xyz/studio).

Want an official verdict instead of a self-run one? `dmoera certify bot.py` runs the sealed exam on our servers - real thresholds, sealed holdout, your enforced attempt count - and issues a hash-bound certificate with a public verify page (no account needed to check it): the exact code, the window, the fee model, the deep-battery gates, all bound to one SHA-256.

## Near-misses worth fixing

249 rejects were profitable out-of-sample and still got rejected - they broke under walk-forward, slippage stress, randomized starts or the holdout. These are the strongest starting points if you want to fork one and fix the gate that killed it. One per mechanism family (the cousins of each are in the repo too):

| Strategy | Domain | OOS return | Died at |
|---|---|---|---|
| [weekend_season_eth_v22](strategies/weekend_season_eth_v22.py) | eth_usdc | +811 bps | holdout |
| [us_open_follow_eth_v46](strategies/us_open_follow_eth_v46.py) | eth_usdc | +712 bps | robustness |
| [session_late_us_gap_sol](strategies/session_late_us_gap_sol.py) | sol_usdc | +666 bps | holdout |
| [confluence_long_eth_v40](strategies/confluence_long_eth_v40.py) | eth_usdc | +611 bps | out-of-sample |
| [cross_eth_spread_btc_v4](strategies/cross_eth_spread_btc_v4.py) | btc_usdc | +596 bps | robustness |
| [rel_strength_eth_v53](strategies/rel_strength_eth_v53.py) | eth_usdc | +509 bps | robustness |
| [session_overnight_handoff_btc_w218](strategies/session_overnight_handoff_btc_w218.py) | btc_usdc | +445 bps | robustness |
| [rsi_div_eth_v38](strategies/rsi_div_eth_v38.py) | eth_usdc | +428 bps | holdout |
| [us_session_mom_btc_v36](strategies/us_session_mom_btc_v36.py) | btc_usdc | +427 bps | robustness |
| [asia_drift_btc_v58](strategies/asia_drift_btc_v58.py) | btc_usdc | +353 bps | robustness |
| [vol_burst_sol_v39](strategies/vol_burst_sol_v39.py) | sol_usdc | +328 bps | robustness |
| [funding_fade_eth_v39](strategies/funding_fade_eth_v39.py) | eth_usdc | +306 bps | out-of-sample |
| [session_asia_handoff_btc](strategies/session_asia_handoff_btc.py) | btc_usdc | +295 bps | holdout |
| [monday_drift_sol_v55](strategies/monday_drift_sol_v55.py) | sol_usdc | +292 bps | out-of-sample |
| [funding_squeeze_btc_v29](strategies/funding_squeeze_btc_v29.py) | btc_usdc | +276 bps | out-of-sample |
| [month_end_eth_w5](strategies/month_end_eth_w5.py) | eth_usdc | +263 bps | robustness |
| [fng_pullback_sol_v36](strategies/fng_pullback_sol_v36.py) | sol_usdc | +245 bps | robustness |
| [scalp_btclead_sol_v49](strategies/scalp_btclead_sol_v49.py) | sol_usdc | +213 bps | out-of-sample |
| [calendar_friday_position_eth_v4](strategies/calendar_friday_position_eth_v4.py) | eth_usdc | +194 bps | holdout |
| [session_late_us_gap_btc_w45](strategies/session_late_us_gap_btc_w45.py) | btc_usdc | +191 bps | holdout |

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to submit a fixed bot.

## What you can do with them

These are real runnable files on the dMoERA strategy contract, not pseudocode:

- **Paste any file into dMoERA Studio** (free paper account at dmoera.xyz) and it runs as-is. Same contract the validator uses. https://www.dmoera.xyz/studio
- **Improve them.** Most failed on robustness, not logic. The card tells you exactly which gate killed it and why. Fix that and you've got a real candidate! You can run real or paper money through these strategies
- **Watch them trade.** Don't want to touch code? The bots that cleared this pipeline run live on the router right now - arena and leaderboard: https://www.dmoera.xyz/arena · https://www.dmoera.xyz/creator/leaderboard
- **Run your own fund.** Manager Mode on dMoERA lets you build a roster from strategies like these and route your own money through it, basically run your own little hedge fund. You can keep your improved strategies private also, just copy the code into the Studio->my strategies->keep private! https://www.dmoera.xyz/managers
- **Submit back to the Arena.** If your improved version passes validation it competes for tournament rewards and competes for routed capital: https://www.dmoera.xyz/arena
- **Risk of Ruin.** You can even load them as strategy cards in a game if you just want to experiment and have fun: https://cachecarti.itch.io/risk-of-ruin , just copy paste the code in the forge there :) Alternate link https://www.dmoera.xyz/riskofruin , Make sure to sign in to save your cards!

Backtest of one of the successful strategies
<img width="960" height="600" alt="image" src="https://github.com/user-attachments/assets/bf5e2fb7-83c2-4ec0-a100-5f7962ce54e5" />

7 stage validation of a failed strategy
<img width="960" height="600" alt="image" src="https://github.com/user-attachments/assets/b0924513-945f-44e1-94c3-8c8085578752" />

## Fair warning

Most of these failed validation for a reason - this is a learning resource and a starting point, not a "deploy these" list. Not financial advice. The 10 in `passed/` are the weakest survivors I'm willing to share; they passed, but they're the bottom of the class.

One honest caveat on the cards: an earlier version of the backtest engine had a bug where a `flat` signal (explicitly close the position) was treated as a no-op by some simulation paths instead of a close. About 1,400 strategies across the full corpus emit `flat` signals, so a narrow set of verdicts may shift a few points under re-screen - the `rescreens` block on each card shows the latest verdict where it exists.

A second caveat: the judge itself keeps getting stricter, so every card is a point-in-time verdict, not a permanent one. Each card now carries `judge_version` and `screened_at` - the whole corpus was screened Oct 9-13, 2026 under judge v1. Since then two changes landed: `ctx.bars` is capped at 1000 bars to match what live bots actually see (Oct 21), and `flat` signals now close positions instead of being ignored (Nov 1). Both only make the judge harsher. If you re-run a bot in the Studio today and it dies earlier than its card says, that's why - the card is stamped to the judge that judged it. 23 bots need more than 1000 bars of history to produce their first signal; those carry a `bar_cap_note` on the card because under the current judge they can't run at all.

Every judge change gets logged in [JUDGE_CHANGELOG.md](JUDGE_CHANGELOG.md) with the date and the direction it moved. That file also carries the placebo calibration: 500 random-entry bots fingerprint-matched to the real batches, pushed through the identical screen - 0/500 passed (twice, on two gate versions). The screen does not admit noise.

MIT license. Take them apart, learn from them, make them better.
