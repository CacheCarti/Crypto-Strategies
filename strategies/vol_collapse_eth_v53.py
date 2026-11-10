from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 12
        self.baseline_period = 168
        self.breakout_period = 12
        self.spike_ratio = 1.8
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.spike_seen = False
        self.spike_bar = -999
        self.max_coil_wait = 36

    def _calc_realized_vol(self, closes, period):
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            c_prev = closes[i - 1]
            if c_prev <= 0:
                return None
            returns.append((closes[i] - c_prev) / c_prev)
        mean_ret = sum(returns) / period
        var = sum((r - mean_ret) ** 2 for r in returns) / period
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = self.baseline_period + self.rv_period + 5
        closes = ctx.closes(warmup)
        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)

        if len(closes) < warmup or len(highs) < self.breakout_period + 2 or len(lows) < self.breakout_period + 2:
            return None

        # Cooldown guard to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate current short-term RV
        current_rv = self._calc_realized_vol(closes, self.rv_period)
        if current_rv is None or current_rv == 0:
            return None

        # Compute historical RV series for the baseline median
        rv_history = []
        stride = 2
        for offset in range(0, self.baseline_period, stride):
            sub_closes = closes[: len(closes) - offset]
            rv = self._calc_realized_vol(sub_closes, self.rv_period)
            if rv is not None:
                rv_history.append(rv)

        if len(rv_history) < 20:
            return None

        rv_sorted = sorted(rv_history)
        baseline_median = rv_sorted[len(rv_sorted) // 2]
        if baseline_median == 0:
            return None

        # State machine tracking chaos -> compression transition
        if current_rv >= baseline_median * self.spike_ratio:
            self.spike_seen = True
            self.spike_bar = ctx.bar_index

        is_compressed = False
        if self.spike_seen:
            if ctx.bar_index - self.spike_bar > self.max_coil_wait:
                self.spike_seen = False
            elif current_rv <= baseline_median * 1.05:
                is_compressed = True

        if not is_compressed:
            return None

        if ctx.has_position():
            return None

        # Breakout verification against prior N bars (excluding the forming bar)
        prior_highs = highs[- (self.breakout_period + 1): -1]
        prior_lows = lows[- (self.breakout_period + 1): -1]
        highest_prev = max(prior_highs)
        lowest_prev = min(prior_lows)
        current_close = ctx.bar.close

        # Trend and crisis market context filters
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.5:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Breakout long
        if current_close > highest_prev and trend_regime != "bear":
            self.spike_seen = False  # Reset coil trigger
            confidence = 0.75 if trend_regime == "bull" else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_expansion_breakout_long",
                    "current_rv": current_rv,
                    "baseline_median_rv": baseline_median,
                    "breakout_level": highest_prev,
                    "close": current_close,
                    "trend_regime": trend_regime
                }
            )

        # Breakdown short
        if current_close < lowest_prev and trend_regime != "bull":
            self.spike_seen = False  # Reset coil trigger
            confidence = 0.75 if trend_regime == "bear" else 0.65
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_expansion_breakdown_short",
                    "current_rv": current_rv,
                    "baseline_median_rv": baseline_median,
                    "breakout_level": lowest_prev,
                    "close": current_close,
                    "trend_regime": trend_regime
                }
            )

        return None