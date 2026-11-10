from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 180,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spike_detected = False
        self.coil_ready = False
        self.bars_in_coil = 0
        self.cooldown_until = 0

    def _realized_vol(self, closes: list, window: int = 12) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes))]
        mean_ret = sum(returns) / window
        var = sum((r - mean_ret) ** 2 for r in returns) / (window - 1) if window > 1 else 0.0
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + 8
        self.spike_detected = False
        self.coil_ready = False
        self.bars_in_coil = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(190)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < 185 or len(highs) < 15:
            return None

        if ctx.has_position():
            return None

        if ctx.bar_index < self.cooldown_until:
            return None

        current_rv = self._realized_vol(closes, 12)
        if current_rv is None:
            return None

        sample_rvs = []
        step = 4
        for i in range(168, 12, -step):
            sub_closes = closes[-i - 12: -i]
            if len(sub_closes) >= 12:
                rv = self._realized_vol(sub_closes, 12)
                if rv is not None:
                    sample_rvs.append(rv)

        if len(sample_rvs) < 15:
            return None

        median_rv = statistics.median(sample_rvs)
        if median_rv <= 0:
            return None

        if current_rv > 2.0 * median_rv:
            self.spike_detected = True
            self.coil_ready = False
            self.bars_in_coil = 0

        if self.spike_detected:
            if current_rv < 1.0 * median_rv:
                self.coil_ready = True
                self.bars_in_coil += 1

                if self.bars_in_coil > 24:
                    self.spike_detected = False
                    self.coil_ready = False
                    self.bars_in_coil = 0
            else:
                self.bars_in_coil += 1
                if self.bars_in_coil > 36:
                    self.spike_detected = False
                    self.coil_ready = False
                    self.bars_in_coil = 0

        if not self.coil_ready:
            return None

        recent_high = max(highs[-13:-1])
        recent_low = min(lows[-13:-1])
        current_close = ctx.bar.close

        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        if current_close > recent_high and btc_ret > -0.015:
            self.spike_detected = False
            self.coil_ready = False
            self.cooldown_until = ctx.bar_index + 12
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_high_break",
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "recent_high": recent_high,
                    "btc_ret": btc_ret
                }
            )

        if current_close < recent_low and btc_ret < 0.015:
            self.spike_detected = False
            self.coil_ready = False
            self.cooldown_until = ctx.bar_index + 12
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_low_break",
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "recent_low": recent_low,
                    "btc_ret": btc_ret
                }
            )

        return None