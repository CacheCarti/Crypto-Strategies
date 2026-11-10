from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "DonchianFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 6
        self.min_wick_atr_mult = 0.35

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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 10)
        highs = ctx.highs(self.channel_period + 10)
        lows = ctx.lows(self.channel_period + 10)

        if len(closes) < self.channel_period + 5:
            return None

        # Manage existing position time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if self.entry_bar > 0 and bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_expired",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Enforce hard cooldown between trade sequences
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis spikes to protect stop hunts from structural meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar channel (excluding current bar)
        prior_highs = highs[-self.channel_period - 1:-1]
        prior_lows = lows[-self.channel_period - 1:-1]

        prev_upper = max(prior_highs)
        prev_lower = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        min_wick = atr * self.min_wick_atr_mult

        # Failed upside breakout: High swept above previous upper by at least min_wick, but close falls back inside
        if curr_high >= prev_upper + min_wick and curr_close < prev_upper:
            wick_penetration = curr_high - prev_upper
            wick_bps = (wick_penetration / prev_upper) * 10000.0
            sl_bps = max(140.0, min(wick_bps * 1.5, 300.0))
            tp_bps = sl_bps * 1.55

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_bear_trap_failed_breakout",
                    "sweep_high": curr_high,
                    "prev_upper": prev_upper,
                    "close": curr_close,
                    "wick_penetration": wick_penetration,
                    "atr": atr,
                }
            )

        # Failed downside breakout: Low swept below previous lower by at least min_wick, but close bounces back inside
        if curr_low <= prev_lower - min_wick and curr_close > prev_lower:
            wick_penetration = prev_lower - curr_low
            wick_bps = (wick_penetration / prev_lower) * 10000.0
            sl_bps = max(140.0, min(wick_bps * 1.5, 300.0))
            tp_bps = sl_bps * 1.55

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_bull_trap_failed_breakout",
                    "sweep_low": curr_low,
                    "prev_lower": prev_lower,
                    "close": curr_close,
                    "wick_penetration": wick_penetration,
                    "atr": atr,
                }
            )

        return None