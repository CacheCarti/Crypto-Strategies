from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 190,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_lookback = 168
        self.breakout_window = 12
        self.spike_threshold = 2.0
        self.max_compression_bars = 24
        self.cooldown_period = 18

        self.last_exit_bar = -999
        self.spike_active = False
        self.compression_active = False
        self.compression_start_bar = -999

    def _calc_vol(self, closes, period=12) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(-period, 0)]
        mean_ret = sum(returns) / period
        var = sum((r - mean_ret) ** 2 for r in returns) / period
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_lookback + 20)
        highs = ctx.highs(self.breakout_window + 2)
        lows = ctx.lows(self.breakout_window + 2)

        if len(closes) < self.median_lookback + 14:
            return None

        # Calculate current short-term realized volatility (12-bar window)
        current_vol = self._calc_vol(closes, period=12)
        if current_vol is None or current_vol == 0:
            return None

        # Calculate historical rolling 12-bar vols over median lookback
        step = 4
        vol_history = []
        for i in range(0, self.median_lookback, step):
            sub_closes = closes[-(i + 13): -i if i > 0 else None]
            v = self._calc_vol(sub_closes, period=12)
            if v is not None:
                vol_history.append(v)

        if len(vol_history) < 15:
            return None

        vol_history.sort()
        median_vol = vol_history[len(vol_history) // 2]
        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol

        # Event state machine: Chaos -> Compression -> Trigger
        if vol_ratio >= self.spike_threshold:
            self.spike_active = True
            self.compression_active = False

        if self.spike_active and vol_ratio < 1.0:
            self.spike_active = False
            self.compression_active = True
            self.compression_start_bar = ctx.bar_index

        if self.compression_active:
            if ctx.bar_index - self.compression_start_bar > self.max_compression_bars:
                self.compression_active = False

        # In-position management
        if ctx.has_position():
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Donchian channel over prior bars (excluding current bar)
        prior_highs = highs[-(self.breakout_window + 1):-1]
        prior_lows = lows[-(self.breakout_window + 1):-1]

        if len(prior_highs) < self.breakout_window or len(prior_lows) < self.breakout_window:
            return None

        hh = max(prior_highs)
        ll = min(prior_lows)
        current_close = ctx.bar.close

        # Entry signal triggers upon breakout from coil
        if self.compression_active:
            if current_close > hh:
                self.compression_active = False
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "coil_breakout_long",
                        "price": current_close,
                        "breakout_high": hh,
                        "vol_ratio": round(vol_ratio, 3),
                        "bars_in_compression": ctx.bar_index - self.compression_start_bar,
                    },
                )
            elif current_close < ll:
                self.compression_active = False
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "coil_breakout_short",
                        "price": current_close,
                        "breakout_low": ll,
                        "vol_ratio": round(vol_ratio, 3),
                        "bars_in_compression": ctx.bar_index - self.compression_start_bar,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index