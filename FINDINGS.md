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

*Historical simulations. Past performance does not predict future results.*
