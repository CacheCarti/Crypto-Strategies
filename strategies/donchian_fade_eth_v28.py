from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.min_wick_atr_mult = 0.35
        self.max_hold_bars = 6
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        total_needed = self.lookback + 15
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Active trade management: time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_reached", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Mandatory cooldown between events
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Exclude crisis regime to prevent fading runaway trends
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar channel (excluding current bar)
        prior_highs = highs[-self.lookback - 1:-1]
        prior_lows = lows[-self.lookback - 1:-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        min_wick = atr * self.min_wick_atr_mult

        # Bear trap (Long setup): poked below channel_low with meaningful wick, but closed inside
        if curr_low < channel_low and curr_close > channel_low:
            wick_depth = channel_low - curr_low
            if wick_depth >= min_wick:
                stop_bps = max(120.0, min(300.0, ((curr_close - curr_low) / curr_close) * 10000.0 + 20.0))
                tp_bps = stop_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bear_trap_donchian_fade",
                        "wick_depth": round(wick_depth, 2),
                        "channel_low": round(channel_low, 2),
                        "atr": round(atr, 2),
                        "close": round(curr_close, 2)
                    }
                )

        # Bull trap (Short setup): poked above channel_high with meaningful wick, but closed inside
        if curr_high > channel_high and curr_close < channel_high:
            wick_depth = curr_high - channel_high
            if wick_depth >= min_wick:
                stop_bps = max(120.0, min(300.0, ((curr_high - curr_close) / curr_close) * 10000.0 + 20.0))
                tp_bps = stop_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bull_trap_donchian_fade",
                        "wick_depth": round(wick_depth, 2),
                        "channel_high": round(channel_high, 2),
                        "atr": round(atr, 2),
                        "close": round(curr_close, 2)
                    }
                )

        return None