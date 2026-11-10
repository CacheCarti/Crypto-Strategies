from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.cooldown_bars = 4
        self.max_hold_bars = 5
        self.min_wick_ratio = 0.25
        self.bars_in_position = 0
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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        highs = ctx.highs(self.period + 2)
        lows = ctx.lows(self.period + 2)

        if len(closes) < self.period + 2:
            return None

        # Manage existing open position
        if ctx.has_position():
            self.bars_in_position += 1
            if self.bars_in_position >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "time_stop_reached",
                    "bars_held": self.bars_in_position,
                    "close": ctx.bar.close
                })
            return None

        # Enforce post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip during severe market disruptions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Exclude the current forming bar to define the true prior breakout levels
        prior_highs = highs[-self.period - 1:-1]
        prior_lows = lows[-self.period - 1:-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        min_wick_abs = atr * self.min_wick_ratio
        current_bar = ctx.bar

        # Bull Trap / Failed Breakout High -> Go Short
        # Intrabar pierced above prior 20-bar high by meaningful wick, but closed back underneath
        bull_trap = (
            current_bar.high > range_high + min_wick_abs and
            current_bar.close < range_high and
            current_bar.close < current_bar.open
        )

        # Bear Trap / Failed Breakdown Low -> Go Long
        # Intrabar dipped below prior 20-bar low by meaningful wick, but closed back above
        bear_trap = (
            current_bar.low < range_low - min_wick_abs and
            current_bar.close > range_low and
            current_bar.close > current_bar.open
        )

        if bull_trap:
            wick_size = current_bar.high - range_high
            risk_dist = current_bar.high - current_bar.close
            stop_bps = max(100.0, min(300.0, (risk_dist / current_bar.close) * 10000.0 + 20.0))
            tp_bps = stop_bps * 1.5

            self.bars_in_position = 0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakout_high_fade",
                    "range_high": round(range_high, 2),
                    "bar_high": round(current_bar.high, 2),
                    "close": round(current_bar.close, 2),
                    "wick_pts": round(wick_size, 2),
                    "atr": round(atr, 2),
                    "stop_bps": round(stop_bps, 1)
                }
            )

        if bear_trap:
            wick_size = range_low - current_bar.low
            risk_dist = current_bar.close - current_bar.low
            stop_bps = max(100.0, min(300.0, (risk_dist / current_bar.close) * 10000.0 + 20.0))
            tp_bps = stop_bps * 1.5

            self.bars_in_position = 0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakdown_low_fade",
                    "range_low": round(range_low, 2),
                    "bar_low": round(current_bar.low, 2),
                    "close": round(current_bar.close, 2),
                    "wick_pts": round(wick_size, 2),
                    "atr": round(atr, 2),
                    "stop_bps": round(stop_bps, 1)
                }
            )

        return None