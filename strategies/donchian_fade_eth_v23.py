from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "Donchian Trap Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.cooldown_bars = 4
        self.max_hold_bars = 6
        self.min_wick_atr_mult = 0.25
        self.entry_bar = -100
        self.last_exit_bar = -100

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
        n = self.lookback + self.atr_period + 2
        closes = ctx.closes(n)
        highs = ctx.highs(n)
        lows = ctx.lows(n)

        if len(closes) < n:
            return None

        # Time-based exit for open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_hold_reached",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis volatility extremes where mean reversion fails
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # 20-bar channel excluding the current bar
        prev_highs = highs[-self.lookback - 1 : -1]
        prev_lows = lows[-self.lookback - 1 : -1]
        channel_high = max(prev_highs)
        channel_low = min(prev_lows)

        curr_bar = ctx.bar
        min_penetration = self.min_wick_atr_mult * atr

        # Bull Trap: Intrabar pierce above Donchian High, close back inside
        bull_trap = (
            curr_bar.high > channel_high + min_penetration
            and curr_bar.close < channel_high
            and curr_bar.close < curr_bar.open  # Bearish close confirms rejection
        )

        # Bear Trap: Intrabar pierce below Donchian Low, close back inside
        bear_trap = (
            curr_bar.low < channel_low - min_penetration
            and curr_bar.close > channel_low
            and curr_bar.close > curr_bar.open  # Bullish close confirms rejection
        )

        if bull_trap:
            wick_size = curr_bar.high - channel_high
            penetration_ratio = round(wick_size / atr, 2)
            confidence = min(0.85, 0.55 + 0.15 * penetration_ratio)

            # Dynamic stop placement past the wick with safety clamp
            sl_bps = min(350.0, max(120.0, ((curr_bar.high - curr_bar.close) / curr_bar.close) * 10000 + 20.0))
            tp_bps = min(500.0, max(180.0, sl_bps * 1.5))

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bull_trap_rejection",
                    "channel_high": channel_high,
                    "bar_high": curr_bar.high,
                    "bar_close": curr_bar.close,
                    "penetration_ratio": penetration_ratio,
                    "atr": atr,
                }
            )

        if bear_trap:
            wick_size = channel_low - curr_bar.low
            penetration_ratio = round(wick_size / atr, 2)
            confidence = min(0.85, 0.55 + 0.15 * penetration_ratio)

            # Dynamic stop placement below the wick with safety clamp
            sl_bps = min(350.0, max(120.0, ((curr_bar.close - curr_bar.low) / curr_bar.close) * 10000 + 20.0))
            tp_bps = min(500.0, max(180.0, sl_bps * 1.5))

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bear_trap_rejection",
                    "channel_low": channel_low,
                    "bar_low": curr_bar.low,
                    "bar_close": curr_bar.close,
                    "penetration_ratio": penetration_ratio,
                    "atr": atr,
                }
            )

        return None