from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 6
        self.max_hold_bars = 8
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
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        closes = ctx.closes(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Check existing position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_exit", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Post-exit cooldown gate to avoid churn and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: skip crisis or extreme meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Reference prior 20-bar window excluding current bar
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        prior_closes = closes[-self.lookback - 1 : -1]

        range_high = max(prior_highs)
        range_low = min(prior_lows)

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        min_penetration = 0.25 * atr

        # Bull trap (failed upside breakout): poked above range high by at least 0.25*ATR, but closed back inside
        bull_trap = (current_high >= range_high + min_penetration) and (current_close < range_high)

        # Bear trap (failed downside breakdown): poked below range low by at least 0.25*ATR, but closed back inside
        bear_trap = (current_low <= range_low - min_penetration) and (current_close > range_low)

        if bull_trap and not bear_trap:
            wick_size = current_high - max(ctx.bar.open, current_close)
            sl_bps = max(120.0, min(300.0, (wick_size / current_close) * 10000.0 * 1.25))
            tp_bps = sl_bps * 1.8
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bull_trap_fade",
                    "range_high": range_high,
                    "wick_high": current_high,
                    "close": current_close,
                    "atr": atr,
                    "sl_bps": sl_bps,
                }
            )

        if bear_trap and not bull_trap:
            wick_size = min(ctx.bar.open, current_close) - current_low
            sl_bps = max(120.0, min(300.0, (wick_size / current_close) * 10000.0 * 1.25))
            tp_bps = sl_bps * 1.8
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bear_trap_fade",
                    "range_low": range_low,
                    "wick_low": current_low,
                    "close": current_close,
                    "atr": atr,
                    "sl_bps": sl_bps,
                }
            )

        return None