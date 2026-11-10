from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.cooldown_bars = 40
        self.max_hold_bars = 16

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 1)
        highs = ctx.highs(self.period + 1)
        lows = ctx.lows(self.period + 1)

        if len(closes) < self.period + 1:
            return None

        # Exclude current forming/closing bar from range calculation to avoid lookahead bias
        ref_highs = highs[-self.period - 1:-1]
        ref_lows = lows[-self.period - 1:-1]
        
        hh = max(ref_highs)
        ll = min(ref_lows)
        midline = (hh + ll) / 2.0
        width = hh - ll

        current_close = ctx.bar.close
        if current_close <= 0:
            return None

        width_bps = (width / current_close) * 10000.0
        pos_dir = ctx.position_direction()

        # Handle active position management (midline target or timeout)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Midline target fade exit
            if pos_dir == "long" and current_close >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_midline_hit",
                    "close": current_close,
                    "midline": midline,
                    "bars_held": bars_held
                })
            elif pos_dir == "short" and current_close <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_midline_hit",
                    "close": current_close,
                    "midline": midline,
                    "bars_held": bars_held
                })
            
            # Time-based scalp safety cutoff
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_timeout",
                    "close": current_close,
                    "bars_held": bars_held
                })

            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety checks
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Channel narrowness filter (sideways chop only: 60 - 200 bps width)
        # Filters out flat dead-markets and trending breakouts (>200 bps)
        if not (60.0 <= width_bps <= 200.0):
            return None

        # Relative range position (0.0 at low, 1.0 at high)
        pct_in_range = (current_close - ll) / width if width > 0 else 0.5
        prev_close = closes[-2]

        # Long: Sweep of channel floor and tick back inside (exhaustion reversal)
        if pct_in_range <= 0.08 and current_close > prev_close:
            self.entry_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, metadata={
                "reason": "range_floor_reversal",
                "width_bps": round(width_bps, 1),
                "pct_in_range": round(pct_in_range, 3),
                "midline": round(midline, 4),
                "hh": round(hh, 4),
                "ll": round(ll, 4)
            })

        # Short: Sweep of channel ceiling and tick back inside
        if pct_in_range >= 0.92 and current_close < prev_close:
            self.entry_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, metadata={
                "reason": "range_ceiling_reversal",
                "width_bps": round(width_bps, 1),
                "pct_in_range": round(pct_in_range, 3),
                "midline": round(midline, 4),
                "hh": round(hh, 4),
                "ll": round(ll, 4)
            })

        return None