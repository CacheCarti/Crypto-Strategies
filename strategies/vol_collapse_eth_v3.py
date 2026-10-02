from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "Post-Spike Coil Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 190,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 10
        self.baseline_period = 168
        self.spike_multiplier = 1.9
        self.donchian_period = 14
        self.max_coil_age = 20
        self.cooldown_bars = 6

        self.was_in_spike = False
        self.coil_armed = False
        self.coil_bars = 0
        self.last_trade_bar = -999

    def _calc_rv(self, closes, period):
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
        if len(returns) < period:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_period + self.rv_period + 5)
        highs = ctx.highs(self.donchian_period + 5)
        lows = ctx.lows(self.donchian_period + 5)

        if len(closes) < self.baseline_period + self.rv_period:
            return None

        # Calculate current RV
        current_rv = self._calc_rv(closes, self.rv_period)
        if current_rv is None:
            return None

        # Sample baseline historical RVs over baseline_period with stride for speed
        stride = 4
        hist_rvs = []
        for offset in range(0, self.baseline_period, stride):
            sub_closes = closes[-(self.rv_period + offset + 1):len(closes) - offset]
            rv_val = self._calc_rv(sub_closes, self.rv_period)
            if rv_val is not None:
                hist_rvs.append(rv_val)

        if len(hist_rvs) < 10:
            return None

        median_rv = statistics.median(hist_rvs)
        if median_rv <= 0:
            return None

        rv_ratio = current_rv / median_rv

        # Spike detection and coil arming state machine
        if rv_ratio >= self.spike_multiplier:
            self.was_in_spike = True
            self.coil_armed = False
            self.coil_bars = 0
        elif self.was_in_spike and rv_ratio < 1.05:
            # Volatility has collapsed back to/below baseline: coil is armed
            self.coil_armed = True
            self.was_in_spike = False
            self.coil_bars = 0

        if self.coil_armed:
            self.coil_bars += 1
            if self.coil_bars > self.max_coil_age:
                self.coil_armed = False

        # If already in position or in cooldown, evaluate exits or skip
        if ctx.has_position():
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Check breakout conditions during active coil compression
        if self.coil_armed and len(highs) >= self.donchian_period + 1:
            recent_high = max(highs[-self.donchian_period - 1:-1])
            recent_low = min(lows[-self.donchian_period - 1:-1])
            curr_close = closes[-1]

            trend_regime = ctx.market.get("trend_regime", "neutral")
            crisis_score = ctx.market.get("crisis_score", 0.0)

            # Avoid initiating new positions during extreme market meltdown
            if crisis_score > 0.75:
                return None

            # Long breakout
            if curr_close > recent_high and trend_regime != "bear":
                self.coil_armed = False
                self.last_trade_bar = ctx.bar_index
                conf = 0.75 if trend_regime == "bull" else 0.65
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_long_breakout",
                        "rv_ratio": round(rv_ratio, 3),
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "donchian_high": round(recent_high, 2),
                        "close": round(curr_close, 2),
                        "coil_bars": self.coil_bars,
                    }
                )

            # Short breakout
            if curr_close < recent_low and trend_regime != "bull":
                self.coil_armed = False
                self.last_trade_bar = ctx.bar_index
                conf = 0.75 if trend_regime == "bear" else 0.65
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_short_breakout",
                        "rv_ratio": round(rv_ratio, 3),
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "donchian_low": round(recent_low, 2),
                        "close": round(curr_close, 2),
                        "coil_bars": self.coil_bars,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.coil_armed = False