from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeHunter(Strategy):
    METADATA = {
        "name": "DonchianFadeHunter",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 10
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
        highs = ctx.highs(self.lookback + self.atr_period + 5)
        lows = ctx.lows(self.lookback + self.atr_period + 5)
        closes = ctx.closes(self.lookback + self.atr_period + 5)

        if len(closes) < self.lookback + self.atr_period + 2:
            return None

        # Manage open position duration
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_exit_reached", "bars_held": bars_held})
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: skip extreme crisis environments
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_bar = ctx.bar
        # Donchian channel evaluated over the prior N bars excluding the current bar
        prev_highs = highs[-(self.lookback + 1):-1]
        prev_lows = lows[-(self.lookback + 1):-1]

        donchian_high = max(prev_highs)
        donchian_low = min(prev_lows)

        # Meaningful wick threshold: at least 0.25 * ATR penetration beyond prior extreme
        min_penetration = 0.25 * atr

        # Setup 1: Bear Trap / Failed Breakdown -> Go Long
        # Price swept below 20-bar low by meaningful amount, but closed safely back inside
        if curr_bar.low < donchian_low - min_penetration and curr_bar.close > donchian_low:
            penetration_bps = ((donchian_low - curr_bar.low) / curr_bar.close) * 10000.0
            # Dynamic stop loss based on the wick low plus buffer, bounded safely
            wick_distance_bps = ((curr_bar.close - curr_bar.low) / curr_bar.close) * 10000.0
            sl_bps = max(120.0, min(wick_distance_bps + 30.0, 240.0))
            tp_bps = sl_bps * 1.75

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakdown_fade",
                    "donchian_low": donchian_low,
                    "bar_low": curr_bar.low,
                    "close": curr_bar.close,
                    "penetration_bps": round(penetration_bps, 1),
                    "atr": round(atr, 2),
                }
            )

        # Setup 2: Bull Trap / Failed Breakout -> Go Short
        # Price swept above 20-bar high by meaningful amount, but closed safely back inside
        if curr_bar.high > donchian_high + min_penetration and curr_bar.close < donchian_high:
            penetration_bps = ((curr_bar.high - donchian_high) / curr_bar.close) * 10000.0
            wick_distance_bps = ((curr_bar.high - curr_bar.close) / curr_bar.close) * 10000.0
            sl_bps = max(120.0, min(wick_distance_bps + 30.0, 240.0))
            tp_bps = sl_bps * 1.75

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_fade",
                    "donchian_high": donchian_high,
                    "bar_high": curr_bar.high,
                    "close": curr_bar.close,
                    "penetration_bps": round(penetration_bps, 1),
                    "atr": round(atr, 2),
                }
            )

        return None