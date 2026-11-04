# Crypto Strategies - 738 rejects + 10 that actually passed

[![Live on dMoERA](https://img.shields.io/badge/live-dMoERA.xyz-7c3aed)](https://www.dmoera.xyz/arena)
[![Validation](https://img.shields.io/badge/validation-7%20stage-orange)](https://www.dmoera.xyz/docs/user/validation-pipeline)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2?logo=discord&logoColor=white)](https://discord.gg/Da2b5er94k)
[![Listed on awesome-quant](https://img.shields.io/badge/listed%20on-awesome--quant-8F3985)](https://github.com/thuquant/awesome-quant#%E7%AD%96%E7%95%A5)

I generated a big batch of crypto trading strategies and ran every single one through a strict 7-stage validation pipeline on real Binance data. This repo holds 748 of them: 738 that got rejected plus 10 that cleared the whole thing. Each one ships with its report card so you can see exactly what it did and where it stood.

Why publish this? Because everyone posts shiny backtests and almost nobody shows the failures. And the passing ones everyone copy trades. I figured a repo with both the corpses with autopsy reports and a few real survivors is more useful than another pile of "7 Sharpe", "profitable" scripts with no receipts.

> New corpse drops weekly through November - the 2,000-bot LLM factory run, the 6.25-year deep battery, and the live A/B that killed our own winners. **Star/watch to follow the drops.**

## What's inside

```
strategies/   738 rejected strategy files (Python)
passed/       10 strategies that cleared the full 7-stage pipeline
cards/        one JSON report card per strategy - stage-by-stage status,
              out-of-sample returns, Sharpe, drawdown, failure class,
              and re-screen verdicts where they exist
index.json    all 748 entries, passed first, then sorted by OOS return
FINDINGS.md   what testing all of this actually taught me
```

## Drops

Corpses don't arrive all at once - every new batch gets screened, killed and published. Roughly one drop a week through November, ~300-400 bots at a time:

| Drop | Date | What landed |
|---|---|---|
| Original corpus | - | 416 strategies (406 rejects + 10 passed), the first 6-month screen |
| Drop 1 | Oct 31, 2026 | +332 hand-tuned batch corpses (v2-v6b, crypto-only) with full code and stage-by-stage autopsies, plus strict re-screen verdicts (`rescreens` block) on all 416 original cards |

## The numbers

- 748 strategies in this repo. 10 passed the original pipeline - under the newer strict re-screen, 0 of them re-passed in strict01 and exactly 1 of the 416 re-passed in strict02 (a former reject). The `rescreens` block on each card has the current verdict. Across everything I ran, roughly 70 cleared validation - the other survivors stay private but most of them belong to the same families as the ones here.
- 234 of the rejects never even survived their own in-sample backtest.
- 170 made money in-sample then died out-of-sample.
- 25 were profitable out-of-sample and STILL got rejected because they broke under walk-forward and slippage stress. A green OOS number on its own means very little.
- Every validation ran on roughly six months of real 5-minute Binance candles, split so the strategy never sees its test windows.

The pattern that kept repeating: families built on context (session timing, fear-and-greed extremes, funding dislocations) generalized. Families built on candle geometry (Donchian breaks, Bollinger squeezes, RSI reversion, orderbook imbalance) memorized. Every BTC scalping variant failed - 0 for 18. At ~7bps a side in fees, the edge you need on 5-minute bars is just brutal. Full breakdown in FINDINGS.md.

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

Every judge change gets logged in [JUDGE_CHANGELOG.md](JUDGE_CHANGELOG.md) with the date and the direction it moved.

MIT license. Take them apart, learn from them, make them better.
