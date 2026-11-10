# Contributing

The most useful contribution is a **resurrection**: take a bot that failed validation, fix the gate that killed it, and show the evidence.

## Submitting a fixed bot

1. Pick a corpse. The [near-misses table](README.md#near-misses-worth-fixing) in the README lists the strongest starting points - profitable out-of-sample but rejected on robustness. Every card tells you exactly which stage killed it and why.
2. Fix it. Keep it on the same strategy contract (`Strategy`, `BarContext`, `Signal`) - no new dependencies, no network calls, no wall-clock reads.
3. Validate it yourself before submitting. Run it through your own out-of-sample split, walk-forward and a fee/slippage stress at 12 bps round trip. A PR that only fixes the in-sample number will be rejected.
4. Open a PR with:
   - the fixed strategy file (new name, e.g. `v4_sol_btcoireg_150_24_fix1.py`)
   - your validation output pasted in the PR description (OOS return, walk-forward result, trade count, fees assumed)
   - one line on what you changed and why you believe it addresses the failure class

## Rules

- **No plagiarism.** Submitting another creator's strategy, with or without modifications, is a permanent ban. Submissions are hash-checked.
- **No look-ahead.** Signals may only use data available at decision time. The safety scanner checks for this.
- **`flat` closes the position.** Under the current judge, a `flat` signal is an explicit close, not a hold.
- **The judge is the judge.** Local results are evidence, not verdicts. Passing locally is necessary, not sufficient - official certification runs on data you have never seen.
- Don't open PRs that only tweak README wording or add links.

## What happens after

Accepted fixes get re-screened under the current judge version. If they pass, they're acknowledged in the PR and may be invited into further testing. Every judge change is logged in [JUDGE_CHANGELOG.md](JUDGE_CHANGELOG.md).

## Other contributions

Found a bug in a card, a mislabeled failure class, or a validation test you think is missing? Open an issue. Suggestions for missing tests are genuinely welcome - every one so far has killed more bots, which is the point.
