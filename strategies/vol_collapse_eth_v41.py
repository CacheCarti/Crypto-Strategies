from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 168
        self.breakout_period = 12
        self.short_vol_len = 8
        self.spike_threshold = 1.8
        self.state = "IDLE"  # IDLE -> SPIKED -> COILING
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.spike_bar = -999
        self.coil_bars_max = 36

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "IDLE"

    def _realized_vol(self, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                continue
            ret = math.log(closes[i] / prev)
            returns.append(ret)
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 20)
        highs = ctx.highs(self.breakout_period + 5)
        lows = ctx.lows(self.breakout_period + 5)

        if len(closes) < self.vol_period + 5 or len(highs) < self.breakout_period + 2:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate fast and baseline volatility
        curr_vol = self._realized_vol(closes, self.short_vol_len)
        base_vol = self._realized_vol(closes, self.vol_period)

        if curr_vol is None or base_vol is None or base_vol == 0:
            return None

        vol_ratio = curr_vol / base_vol

        # Update state machine tracking spike -> coil
        if self.state == "IDLE":
            if vol_ratio >= self.spike_threshold:
                self.state = "SPIKED"
                self.spike_bar = ctx.bar_index
        elif self.state == "SPIKED":
            if ctx.bar_index - self.spike_bar > self.coil_bars_max:
                self.state = "IDLE"
            elif vol_ratio <= 1.0:
                self.state = "COILING"
        elif self.state == "COILING":
            if ctx.bar_index - self.spike_bar > self.coil_bars_max:
                self.state = "IDLE"
            elif vol_ratio > 1.3:
                # Volatility re-expanded without a clean coil setup
                self.state = "SPIKED"
                self.spike_bar = ctx.bar_index

        if ctx.has_position():
            return None

        # Entry logic: Only trade coil releases
        if self.state == "COILING":
            # Donchian channel over previous 12 bars (excluding current bar)
            prior_high = max(highs[-self.breakout_period - 1 : -1])
            prior_low = min(lows[-self.breakout_period - 1 : -1])
            curr_close = ctx.bar.close

            regime = ctx.market.get("regime", "NORMAL")
            if regime == "MELTDOWN":
                return None

            if curr_close > prior_high:
                self.state = "IDLE"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "post_spike_coil_bullish_release",
                        "vol_ratio": round(vol_ratio, 3),
                        "prior_high": round(prior_high, 2),
                        "close": round(curr_close, 2),
                        "state_duration": ctx.bar_index - self.spike_bar,
                    },
                )
            elif curr_close < prior_low:
                self.state = "IDLE"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "post_spike_coil_bearish_release",
                        "vol_ratio": round(vol_ratio, 3),
                        "prior_low": round(prior_low, 2),
                        "close": round(curr_close, 2),
                        "state_duration": ctx.bar_index - self.spike_bar,
                    },
                )

        return None