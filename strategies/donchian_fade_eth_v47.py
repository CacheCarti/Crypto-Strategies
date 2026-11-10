from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeTrap(Strategy):
    METADATA = {
        "name": "Donchian Trap Reversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 4
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 5:
            return None

        # Check timed exit on open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_based_exit", "bars_held": bars_held}
                )
            return None

        # Strict post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Exclude extreme market crisis to prevent fading structural breaks
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # The prior 20-bar Donchian range (excluding current bar)
        prior_highs = highs[-self.lookback - 1:-1]
        prior_lows = lows[-self.lookback - 1:-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        current_high = highs[-1]
        current_low = lows[-1]
        current_close = closes[-1]

        # Require a meaningful wick piercing the band (at least 0.2 ATR) but closing back inside
        min_penetration = 0.20 * atr

        # Bull trap: high breached resistance with meaningful penetration, but closed back under
        bear_trap_high = (current_high - channel_high) >= min_penetration and (current_close < channel_high)

        # Bear trap: low breached support with meaningful penetration, but closed back above
        bull_trap_low = (channel_low - current_low) >= min_penetration and (current_close > channel_low)

        # Volatility-based structural risk management (stop beyond wick with buffer)
        if bear_trap_high:
            wick_risk = current_high - current_close
            sl_bps = max(120.0, min(300.0, ((wick_risk + 0.15 * atr) / current_close) * 10000.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "bull_trap_fade",
                    "channel_high": round(channel_high, 2),
                    "wick_high": round(current_high, 2),
                    "close": round(current_close, 2),
                    "penetration_atr": round((current_high - channel_high) / atr, 2),
                }
            )

        if bull_trap_low:
            wick_risk = current_close - current_low
            sl_bps = max(120.0, min(300.0, ((wick_risk + 0.15 * atr) / current_close) * 10000.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "bear_trap_fade",
                    "channel_low": round(channel_low, 2),
                    "wick_low": round(current_low, 2),
                    "close": round(current_close, 2),
                    "penetration_atr": round((channel_low - current_low) / atr, 2),
                }
            )

        return None