# Findings - what survives and what doesn't

Hundreds of strategies generated and run through the same 7-stage pipeline. The pass rate ran around 5%. This is what the data showed about which kinds of crypto edges are real and which are fiction.

## Family survival

Each family is ~9 parameter variants of one strategy idea:

| Family | Passed | What it does |
|---|---|---|
| asia_drift_btc | 7/9 | Asian session drift timing - best family by far |
| fng_pullback_sol | 5/9 | Buys SOL pullbacks at fear extremes |
| crash_recovery_* | 6/27 | Catches post-crash bounces - real but finicky |
| ema_pullback_eth | 3/9 | Classic trend pullback |
| funding_fade / fng_mom / fng_contrarian | 3/9 each | Sentiment and funding conditioning holds up |
| donchian_vol, bb_squeeze, rsi2_revert, book_imb_mom, confluence_long, higher_low, cross_regime, fng_dca, atr_exp, btc_lead | 0/9 | Whole families wiped out |
| BTC scalps | 0/18 | Nothing at 5-min survives the friction |

## The four killers

**1. Curve fitting.** 234 died in-sample, 170 more died out-of-sample. The dominant failure mode is memorizing the training window. If your backtest looks great, odds are you fit noise.

**2. A positive OOS test is not proof.** 25 strategies were profitable out-of-sample and still got rejected. One good test window can be luck. The survivors showed the edge held across rolling walk-forward windows, randomized starts, and extra injected slippage.

**3. Friction eats thin edges.** Breakout and mean reversion families made money gross and went negative at ~7 bps a side. That is why every 5-minute BTC scalp failed. The bar for per-trade edge at retail fee levels is much higher than people think.

**4. Context beats pattern geometry.** The survivors keyed off when to trade - session timing, sentiment extremes, funding dislocations. The losers keyed off what the candles looked like. Context-aware entries generalized. Pattern entries memorized.

## How the pipeline worked

Same harness for every strategy, no exceptions: static safety scan, in-sample backtest, out-of-sample test, walk-forward across rolling windows, randomized start points, perturbation with extra slippage, then a final holdout on data nothing else ever touched. Sandboxed execution, realistic fees, no lookahead, seeded RNG.

Most survivors are not in this repo - only the 10 weakest are public (see passed/). The point of the repo is the failures, but the passers are the proof the gate is real.

## The re-screen: the judge got meaner

After the original screen, the harness got stricter - holdout enforced, tighter OOS bars, more gates required. We re-ran all 416 public strategies through two strict configs. Results are on every card in the `rescreens` block:

- **strict01:** 0 of 416 re-passed - including none of the original 10 in `passed/`.
- **strict02:** exactly 1 re-passed, and it was one of the rejects: `weekend_season_eth_v6` cleared all 7 stages.

### So were the original 10 passes any good?

Honestly: mostly no, under the stricter judge. Re-screened, all 10 die at the out-of-sample gate, and most die with a *negative* OOS return. The best of them (`rel_strength_eth_v3`) got as far as stage 5 and died at holdout with +234 bps OOS; the rest topped out at stage 2-3 with -85 to -900 bps. A pass under the old judge meant "survived 6 months of 5-minute bars" - it did not mean the edge was robust. Meanwhile the only strategy to clear strict02 was a bot we'd already buried (`weekend_season_eth_v6`).

Verdicts are a property of the gate as much as the strategy - that's why every card keeps its re-screen history instead of overwriting the original verdict.

## Drop 1: the hand-tuned batches

+332 corpses from the v2-v6b manual batches (Oct 2026), crypto-only, full code + stage-by-stage autopsy. Of the 384 crypto strategies screened in that era, 52 survived and stay private - the rejects published here are parameter neighbors of live strategies, so they show exactly how close the gate gets.

Admits per batch (crypto domains only - gold/oil/TRX batches excluded from this repo):

| Batch | Screened | Admitted | Dead (published) |
|---|---|---|---|
| v2 | 42 | 5 | 37 |
| v3d | 26 | 0 | 26 |
| v3h | 96 | 0 | 96 |
| v4 | 92 | 3 | 89 |
| v5 | 52 | 17 | 35 |
| v6 | 60 | 18 | 42 |
| v6b | 16 | 9 | 7 |
| **Total** | **384** | **52** | **332** |

Worth noting: the admit rate is meaningless across batches - v5/v6/v6b were deliberately narrow batches built around one mechanism that was already working, so they look "good" (35-56% admits). v3h was a broad sweep of new ideas and died 96-for-96. The batch-level number reflects how the batch was generated, not how smart it was.

*Historical simulations. Past performance does not predict future results.*

---

## Try it yourself

Every bot in this repo runs as-is on the same contract the validator used:

- **dMoERA Studio** (free paper account): paste any strategy file, it runs immediately - https://www.dmoera.xyz/studio
- **Watch the survivors live**: https://www.dmoera.xyz/arena
- **How the pipeline works**: https://www.dmoera.xyz/docs/user/how-it-works
- **Fix one and submit it**: improved versions that clear validation compete for routed capital - https://www.dmoera.xyz/arena

*Historical simulations. Past performance does not predict future results. Not financial advice.*
