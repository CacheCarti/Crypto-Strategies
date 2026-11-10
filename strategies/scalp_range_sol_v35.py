from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 6
        self.max_hold_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 5)
        if len(closes) < self.channel_period + 1:
            return None

        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)
        curr_price = ctx.bar.close

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        window_highs = highs[-self.channel_period:]
        window_lows = lows[-self.channel_period:]
        chan_high = max(window_highs)
        chan_low = min(window_lows)
        chan_range = chan_high - chan_low
        midline = (chan_high + chan_low) / 2.0

        if chan_range <= 0:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Timed exit to prevent getting trapped in creeping breakdowns/breakouts
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "timed_scalp_exit",
                    "bars_held": bars_held,
                    "curr_price": curr_price,
                    "midline": midline
                })

            # Midline target fade completion
            if pos_dir == "long" and curr_price >= midline:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "midline_reached_long",
                    "curr_price": curr_price,
                    "midline": midline
                })
            elif pos_dir == "short" and curr_price <= midline:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "midline_reached_short",
                    "curr_price": curr_price,
                    "midline": midline
                })
            return None

        # Mandatory cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime filter: Stand down in high volatility / crisis or wide expanding trend
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("HIGH_VOL", "CRISIS", "MELTDOWN"):
            return None

        # Range compression check: channel must be narrow chop (tight relative to ATR)
        range_bps = (chan_range / curr_price) * 10000.0
        atr_bps = (atr / curr_price) * 10000.0
        
        # Chop filter: channel height should be compressed relative to price action
        if range_bps > 220.0 or range_bps < 40.0:
            return None
        if chan_range > 3.2 * atr:
            return None

        # Relative location inside channel
        loc = (curr_price - chan_low) / chan_range

        # Edge fade: Long extreme bottom (< 10%), Short extreme top (> 90%)
        # Structure-based dynamic stops: 1.2x ATR
        dyn_sl_bps = max(60.0, min(140.0, (1.2 * atr / curr_price) * 10000.0))
        dyn_tp_bps = max(70.0, (abs(curr_price - midline) / curr_price) * 10000.0)

        if loc <= 0.10 and ctx.bar.close > ctx.bar.open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=900,
                metadata={
                    "reason": "range_bottom_bounce_reversion",
                    "loc": round(loc, 3),
                    "chan_high": round(chan_high, 2),
                    "chan_low": round(chan_low, 2),
                    "range_bps": round(range_bps, 1),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        if loc >= 0.90 and ctx.bar.close < ctx.bar.open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=900,
                metadata={
                    "reason": "range_top_rejection_reversion",
                    "loc": round(loc, 3),
                    "chan_high": round(chan_high, 2),
                    "chan_low": round(chan_low, 2),
                    "range_bps": round(range_bps, 1),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        return None