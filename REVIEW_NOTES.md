# REVIEW NOTES - v2 private copy (funnel pass)

This is a private review copy of the Crypto-Strategies repo with the funnel
gaps fixed. Nothing here is public yet - push to a NEW private GitHub repo
to review it rendered, or just read the diffs locally.

## What changed vs the live repo

1. README.md - top block added: two badges (live site + validation docs),
   a "watch the survivors trade live" line with arena/leaderboard/studio
   links, and a star-hook for the weekly drops ("star/watch to follow").
2. README.md - CTA section: added a passive "watch them trade" link
   (biggest audience), linked Arena (/arena) and Manager Mode (/managers)
   which were previously mentioned but never hyperlinked.
3. FINDINGS.md - added a "Try it yourself" footer. Previously the most
   carefully-read file in the repo dead-ended at the disclaimer.
4. cards/*.json - every card now carries `"run_in_studio":
   "https://www.dmoera.xyz/studio"` so programmatic consumers hit the
   funnel too.

## What still needs YOU (can't be done from a file copy)

1. Push to a private repo: GitHub → New repository → Private → upload.
2. GITHUB TOPICS on the repo (Settings → Topics, or the About gear):
   `algorithmic-trading` `backtesting` `cryptocurrency` `quant`
   `trading-strategies` `validation` `algo-trading` `python`
   - this is how the repo surfaces in github.com/topics and search.
3. LIVE BADGE (needs a small backend endpoint): a shields.io endpoint on
   dmoera.xyz that returns live stats, e.g. router P&L or "N strategies
   live". Then replace the static "live" badge in the README with:
   `https://img.shields.io/endpoint?url=https://www.dmoera.xyz/api/badge`
   - the README then shows a number that moves by itself. Spec'd in
   DEMON_2026_10_31_DISCOVERY_FUNNEL.md.
4. Verify every URL renders before the public push (the /managers route
   is browse-only; /creator/leaderboard, /arena, /studio are confirmed
   live routes).

## Deliberately NOT changed

- FINDINGS.md body and the card contents - the data stays byte-identical.
- The flat-signal caveat is intentionally NOT in this repo yet: it's a
  screen-era issue, and the disclosure is already in the Reddit drip
  posts. If we want it here too, one line under "Fair warning" covers it.
