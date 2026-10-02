from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 10
        self.baseline_period = 160
        self.breakout_period = 10
        self.spike_mult = 1.85
        self.cooldown_bars = 8
        self.max_coil_age = 24

        self.last_exit_bar = -999
        self.saw_spike = False
        self.spike_bar = -999
        self.in_coil = False
        self.coil_start_bar = -999

    def _realized_vol(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                continue
            r = (closes[i] - prev) / prev
            returns.append(r)
        if len(returns) < period:
            return None
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.in_coil = False
        self.saw_spike = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.baseline_period + self.vol_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Current short-term realized vol
        current_vol = self._realized_vol(closes, self.vol_period)
        if current_vol is None:
            return None

        # Historical short-term vol series for baseline calculation
        vol_history = []
        step = 2
        for i in range(self.baseline_period, 0, -step):
            sub_closes = closes[:-i]
            v = self._realized_vol(sub_closes, self.vol_period)
            if v is not None:
                vol_history.append(v)

        if len(vol_history) < 20:
            return None

        vol_history.sort()
        median_vol = vol_history[len(vol_history) // 2]
        if median_vol <= 1e-6:
            return None

        vol_ratio = current_vol / median_vol

        # Track spike state
        if vol_ratio >= self.spike_mult:
            self.saw_spike = True
            self.spike_bar = ctx.bar_index
            self.in_coil = False

        # Transition to coil: was spiking within last 36 bars, now collapsed below median
        if self.saw_spike and (ctx.bar_index - self.spike_bar <= 36):
            if current_vol <= median_vol * 1.05:
                self.in_coil = True
                self.coil_start_bar = ctx.bar_index
                self.saw_spike = False

        # Expire coil if too old
        if self.in_coil and (ctx.bar_index - self.coil_start_bar > self.max_coil_age):
            self.in_coil = False

        # Manage open position
        if ctx.has_position():
            return None

        # If primed in coil regime, evaluate breakout triggers
        if not self.in_coil:
            return None

        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)
        if len(highs) < self.breakout_period + 2 or len(lows) < self.breakout_period + 2:
            return None

        # Look back excluding current bar
        prior_high = max(highs[-self.breakout_period - 1:-1])
        prior_low = min(lows[-self.breakout_period - 1:-1])
        current_price = ctx.bar.close

        # Trend filter from market context
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Breakout Long
        if current_price > prior_high and trend_regime != "bear":
            self.in_coil = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_high_breakout",
                    "price": current_price,
                    "prior_high": prior_high,
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "coil_age": ctx.bar_index - self.coil_start_bar,
                }
            )

        # Breakdown Short
        if current_price < prior_low and trend_regime != "bull":
            self.in_coil = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_low_breakdown",
                    "price": current_price,
                    "prior_low": prior_low,
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "coil_age": ctx.bar_index - self.coil_start_bar,
                }
            )

        return None