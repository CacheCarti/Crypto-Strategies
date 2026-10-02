# Crypto Strategies - 406 rejects + 10 that actually passed

I generated a big batch of crypto trading strategies and ran every single one through a strict 7-stage validation pipeline on 6 months of real Binance data. This repo holds 416 of them: 406 that got rejected plus 10 that cleared the whole thing. Each one ships with its report card so you can see exactly what it did and where it stood.

Why publish this? Because everyone posts shiny backtests and almost nobody shows the failures. And the passing ones everyone copy trades. I figured a repo with both the corpses with autopsy reports and a few real survivors is more useful than another pile of "7 Sharpe", "profitable" scripts with no receipts.

## What's inside

```
strategies/   406 rejected strategy files (Python)
passed/       10 strategies that cleared the full 7-stage pipeline
cards/        one JSON report card per strategy - in-sample and out-of-sample
              returns, Sharpe, drawdown, the stage it reached, the verdict
index.json    all 416 entries, passed first, then sorted by OOS return
FINDINGS.md   what testing all of this actually taught me
```

## The numbers

- 416 strategies in this repo. 10 passed the full pipeline, so about a 2.4% hit rate on what's public. Across everything I ran, roughly 20 cleared validation, call it a ~4.8% hit rate. The other survivors stay private but most of them belong to the same families as the ones here.
- 234 of the rejects never even survived their own in-sample backtest.
- 170 made money in-sample then died out-of-sample.
- 25 were profitable out-of-sample and STILL got rejected because they broke under walk-forward and slippage stress. A green OOS number on its own means very little.
- Every validation ran on roughly six months of real 5-minute Binance candles, split so the strategy never sees its test windows.

The pattern that kept repeating: families built on context (session timing, fear-and-greed extremes, funding dislocations) generalized. Families built on candle geometry (Donchian breaks, Bollinger squeezes, RSI reversion, orderbook imbalance) memorized. Every BTC scalping variant failed - 0 for 18. At ~7bps a side in fees, the edge you need on 5-minute bars is just brutal. Full breakdown in FINDINGS.md.

## What you can do with them

These are real runnable files on the dMoERA strategy contract, not pseudocode:

- **Paste any file into dMoERA Studio** (free paper account at dmoera.xyz) and it runs as-is. Same contract the validator uses. https://www.dmoera.xyz/studio
- **Improve them.** Most failed on robustness, not logic. The card tells you exactly which gate killed it and why. Fix that and you've got a real candidate! You can run real or paper money through these strategies
- **Run your own fund.** Manager Mode on dMoERA lets you build a roster from strategies like these and route your own money through it, basically run your own little hedge fund. You can keep your improved strategies private also, just copy the code into the Studio->my strategies->keep private!
- **Submit back to the Arena.** If your improved version passes validation it competes for tournament rewards and competes for routed capital.
- **Risk of Ruin.** You can even load them as strategy cards in a game if you just want to experiment and have fun: https://cachecarti.itch.io/risk-of-ruin , just copy paste the code in the forge there :) Alternate link https://www.dmoera.xyz/riskofruin , Make sure to sign in to save your cards!

Backtest of one of the successful strategies
<img width="960" height="600" alt="image" src="https://github.com/user-attachments/assets/bf5e2fb7-83c2-4ec0-a100-5f7962ce54e5" />

7 stage validation of a failed strategy
<img width="960" height="600" alt="image" src="https://github.com/user-attachments/assets/b0924513-945f-44e1-94c3-8c8085578752" />


## Fair warning

Most of these failed validation for a reason - this is a learning resource and a starting point, not a "deploy these" list. Not financial advice. The 10 in `passed/` are the weakest survivors I'm willing to share; they passed, but they're the bottom of the class.

MIT license. Take them apart, learn from them, make them better.
