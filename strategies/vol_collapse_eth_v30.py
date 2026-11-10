from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 14
        self.baseline_window = 168  # 1 week of 1h bars
        self.channel_period = 12
        self.cooldown_bars = 8
        self.max_coil_wait = 24

        self.last_exit_bar = -999
        self.spike_active = False
        self.coil_armed = False
        self.coil_armed_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_armed = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_window + self.vol_period + 2)
        if len(closes) < self.baseline_window + self.vol_period + 1:
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute rolling realized volatility (standard deviation of log returns)
        # Using a rolling window of recent realized vol observations to find median baseline
        n_obs = self.baseline_window
        rolling_vols = []
        for offset in range(n_obs):
            # End index for this slice
            end_idx = len(closes) - offset
            start_idx = end_idx - self.vol_period - 1
            log_returns = [
                math.log(closes[j] / closes[j - 1])
                for j in range(start_idx + 1, end_idx)
            ]
            mean_ret = sum(log_returns) / len(log_returns)
            var = sum((r - mean_ret) ** 2 for r in log_returns) / len(log_returns)
            rolling_vols.append(math.sqrt(var))

        current_vol = rolling_vols[0]
        sorted_vols = sorted(rolling_vols)
        median_vol = sorted_vols[len(sorted_vols) // 2]

        if median_vol <= 1e-8:
            return None

        vol_ratio = current_vol / median_vol

        # Track spike and collapse state transitions
        if vol_ratio >= 2.0:
            self.spike_active = True

        # When vol collapses back below median after an established spike
        if self.spike_active and vol_ratio < 1.0:
            self.spike_active = False
            self.coil_armed = True
            self.coil_armed_bar = ctx.bar_index

        # Expire coil if it remains dormant too long
        if self.coil_armed and (ctx.bar_index - self.coil_armed_bar > self.max_coil_wait):
            self.coil_armed = False

        if not self.coil_armed or ctx.has_position():
            return None

        # Price channel breakout for release trigger
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)
        if len(highs) < self.channel_period + 1 or len(lows) < self.channel_period + 1:
            return None

        # Use the range of the bars preceding the current breakout candle
        breakout_high = max(highs[:-1])
        breakout_low = min(lows[:-1])
        curr_close = ctx.bar.close

        # Stricter boundary: only trade trend direction aligned with broader market regime
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long coil release
        if curr_close > breakout_high and trend_regime != "bear":
            self.coil_armed = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "breakout_high": breakout_high,
                    "trend_regime": trend_regime,
                    "close": curr_close,
                },
            )

        # Short coil release
        if curr_close < breakout_low and trend_regime != "bull":
            self.coil_armed = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "breakout_low": breakout_low,
                    "trend_regime": trend_regime,
                    "close": curr_close,
                },
            )

        return None