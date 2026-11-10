from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 6
        self.min_width_bps = 35.0
        self.max_width_bps = 110.0

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
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 2:
            return None

        # Manage active position: midline target or time-stop
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close
            
            # 30-bar prior channel midline
            prior_high = max(highs[-self.channel_period - 1:-1])
            prior_low = min(lows[-self.channel_period - 1:-1])
            midline = (prior_high + prior_low) / 2.0
            pos_dir = ctx.position_direction()

            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "timed_scalp_exit",
                    "bars_held": bars_in_trade,
                    "close": current_close,
                    "midline": midline
                })

            if pos_dir == "long" and current_close >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "long_hit_midline",
                    "close": current_close,
                    "midline": midline
                })
            elif pos_dir == "short" and current_close <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "short_hit_midline",
                    "close": current_close,
                    "midline": midline
                })
            return None

        # Mandatory cooldown after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid high volatility or crisis market regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # Prior window excludes current bar to avoid lookahead/repainting
        chan_highs = highs[-self.channel_period - 1:-1]
        chan_lows = lows[-self.channel_period - 1:-1]
        c_high = max(chan_highs)
        c_low = min(chan_lows)
        mid = (c_high + c_low) / 2.0

        if mid <= 0:
            return None

        width_bps = ((c_high - c_low) / mid) * 10000.0

        # Structural filter: Channel must be sufficiently compressed (chop), not trending/expanding
        if width_bps < self.min_width_bps or width_bps > self.max_width_bps:
            return None

        # Volatility check: ATR should be relatively low relative to the channel
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_bps = (atr / mid) * 10000.0
        if atr_bps > 45.0:
            return None

        close = ctx.bar.close
        high = ctx.bar.high
        low = ctx.bar.low

        # Structural stop: dynamic based on channel boundary + buffer
        edge_buffer_bps = 25.0

        # Fade lower edge: price pierced near low but closed back inside
        if low <= c_low and close > c_low:
            dist_to_mid = ((mid - close) / close) * 10000.0
            if dist_to_mid >= 20.0:
                sl_bps = max(40.0, min(100.0, ((close - c_low) / close) * 10000.0 + edge_buffer_bps))
                tp_bps = max(50.0, min(130.0, dist_to_mid))
                self.entry_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75, stop_loss_bps=sl_bps, take_profit_bps=tp_bps, metadata={
                    "reason": "fade_channel_low_rejection",
                    "channel_low": c_low,
                    "channel_high": c_high,
                    "width_bps": round(width_bps, 1),
                    "close": close,
                    "atr_bps": round(atr_bps, 1)
                })

        # Fade upper edge: price pierced near high but closed back inside
        if high >= c_high and close < c_high:
            dist_to_mid = ((close - mid) / close) * 10000.0
            if dist_to_mid >= 20.0:
                sl_bps = max(40.0, min(100.0, ((c_high - close) / close) * 10000.0 + edge_buffer_bps))
                tp_bps = max(50.0, min(130.0, dist_to_mid))
                self.entry_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75, stop_loss_bps=sl_bps, take_profit_bps=tp_bps, metadata={
                    "reason": "fade_channel_high_rejection",
                    "channel_low": c_low,
                    "channel_high": c_high,
                    "width_bps": round(width_bps, 1),
                    "close": close,
                    "atr_bps": round(atr_bps, 1)
                })

        return None