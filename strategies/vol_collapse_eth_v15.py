from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoil(Strategy):
    METADATA = {
        "name": "PostSpikeCoil",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.spike_active = False
        self.coiling = False
        self.coil_start_bar = -100
        self.max_coil_duration = 36

    def _calc_rv(self, closes: list, period: int = 12) -> float:
        if len(closes) < period + 1:
            return 0.0
        log_rets = []
        for i in range(len(closes) - period, len(closes)):
            p0 = closes[i - 1]
            p1 = closes[i]
            if p0 > 0 and p1 > 0:
                log_rets.append(math.log(p1 / p0))
            else:
                log_rets.append(0.0)
        mean_ret = sum(log_rets) / len(log_rets)
        var = sum((r - mean_ret) ** 2 for r in log_rets) / len(log_rets)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiling = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(200)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate short-term realized vol (12 bars) across historical window
        rv_window = 168
        rv_history = []
        step = 4
        for offset in range(rv_window, -1, -step):
            idx_end = len(closes) - offset
            if idx_end >= 13:
                sub_closes = closes[:idx_end]
                rv_history.append(self._calc_rv(sub_closes, period=12))

        if len(rv_history) < 15:
            return None

        current_rv = self._calc_rv(closes, period=12)
        sorted_rv = sorted(rv_history)
        median_rv = sorted_rv[len(sorted_rv) // 2]

        if median_rv <= 0:
            return None

        # State machine tracking Spike -> Collapse into Coil
        if current_rv >= 2.0 * median_rv:
            self.spike_active = True
            self.coiling = False
        elif self.spike_active and current_rv <= median_rv * 0.95:
            self.spike_active = False
            self.coiling = True
            self.coil_start_bar = ctx.bar_index

        # Expire coil if it stalls without breakout
        if self.coiling and (ctx.bar_index - self.coil_start_bar > self.max_coil_duration):
            self.coiling = False

        if ctx.has_position():
            # Time-based exit protection beyond target hold
            bars_in_pos = ctx.bar_index - self.last_exit_bar
            if bars_in_pos > 24:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "horizon_flat_timeout"})
            return None

        if not self.coiling:
            return None

        # Avoid entry during extreme market distress
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Coil breakout of 12-bar Donchian channel (excluding current bar)
        lookback = 12
        prior_high = max(highs[-lookback - 1:-1])
        prior_low = min(lows[-lookback - 1:-1])
        current_close = ctx.bar.close

        # Long Breakout
        if current_close > prior_high:
            self.coiling = False
            trend = ctx.market.get("trend_regime", "neutral")
            conf = 0.85 if trend == "bull" else 0.70
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_long_breakout",
                    "current_rv": round(current_rv, 6),
                    "median_rv": round(median_rv, 6),
                    "prior_high": prior_high,
                    "close": current_close,
                    "regime": regime
                }
            )

        # Short Breakout
        if current_close < prior_low:
            self.coiling = False
            trend = ctx.market.get("trend_regime", "neutral")
            conf = 0.85 if trend == "bear" else 0.70
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_short_breakout",
                    "current_rv": round(current_rv, 6),
                    "median_rv": round(median_rv, 6),
                    "prior_low": prior_low,
                    "close": current_close,
                    "regime": regime
                }
            )

        return None