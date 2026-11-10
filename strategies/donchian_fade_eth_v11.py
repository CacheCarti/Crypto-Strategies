from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.min_wick_atr_mult = 0.25
        self.cooldown_bars = 4
        self.max_hold_bars = 6
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
        self.entry_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.atr_period + 2)
        if len(closes) < self.lookback + self.atr_period + 2:
            return None

        highs = ctx.highs(self.lookback + self.atr_period + 2)
        lows = ctx.lows(self.lookback + self.atr_period + 2)

        # Active position management: time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_decay_exit", "bars_held": bars_held, "price": ctx.bar.close})
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Reference Donchian channel excluding current bar
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        min_wick = self.min_wick_atr_mult * atr

        # Bull trap (Failed upside breakout -> Short)
        # Price spiked above range_high by at least min_wick, but close surrendered back below range_high
        if current_high >= range_high + min_wick and current_close < range_high:
            wick_size = current_high - max(ctx.bar.open, current_close)
            if wick_size >= min_wick:
                sl_bps = min(max((current_high - current_close) / current_close * 10000.0 + 30.0, 120.0), 280.0)
                tp_bps = sl_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "donchian_bull_trap_fade",
                        "range_high": range_high,
                        "wick_size": wick_size,
                        "atr": atr,
                        "price": current_close
                    }
                )

        # Bear trap (Failed downside breakdown -> Long)
        # Price spiked below range_low by at least min_wick, but close reclaimed back above range_low
        if current_low <= range_low - min_wick and current_close > range_low:
            wick_size = min(ctx.bar.open, current_close) - current_low
            if wick_size >= min_wick:
                sl_bps = min(max((current_close - current_low) / current_close * 10000.0 + 30.0, 120.0), 280.0)
                tp_bps = sl_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "donchian_bear_trap_fade",
                        "range_low": range_low,
                        "wick_size": wick_size,
                        "atr": atr,
                        "price": current_close
                    }
                )

        return None