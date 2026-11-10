from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 175,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.breakout_period = 12
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.spike_seen = False
        self.armed_coil = False
        self.armed_counter = 0
        self.max_armed_bars = 24

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.armed_coil = False
        self.spike_seen = False
        self.armed_counter = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + 20)
        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)
        
        if len(closes) < self.median_period + 15:
            return None

        # Manage armed state timeout
        if self.armed_coil:
            self.armed_counter += 1
            if self.armed_counter > self.max_armed_bars:
                self.armed_coil = False
                self.spike_seen = False
                self.armed_counter = 0

        # Compute log returns & rolling 8-bar realized volatility
        # Fast rolling realized vol (standard deviation of returns over 8 bars)
        returns = []
        for i in range(1, len(closes)):
            c_prev = closes[i - 1]
            if c_prev > 0:
                returns.append(math.log(closes[i] / c_prev))
            else:
                returns.append(0.0)

        rv_window = 8
        if len(returns) < self.median_period + rv_window:
            return None

        # Build series of rolling RVs over median_period
        rv_series = []
        for idx in range(len(returns) - self.median_period, len(returns) + 1):
            sub = returns[idx - rv_window:idx]
            mean = sum(sub) / len(sub)
            var = sum((r - mean) ** 2 for r in sub) / len(sub)
            rv_series.append(math.sqrt(var))

        current_rv = rv_series[-1]
        sorted_rv = sorted(rv_series[:-1])
        median_rv = sorted_rv[len(sorted_rv) // 2]

        if median_rv <= 1e-7:
            return None

        # State machine: Chaos (RV > 2x median) -> Compression (RV falls back <= 1.05x median)
        if current_rv > 2.0 * median_rv:
            self.spike_seen = True

        if self.spike_seen and current_rv <= 1.05 * median_rv:
            self.armed_coil = True
            self.spike_seen = False
            self.armed_counter = 0

        # Exit/Flat checks for open position
        if ctx.has_position():
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Only look for coil release if state is armed
        if not self.armed_coil:
            return None

        # Regime gate to fix prior alpha bleed: avoid crisis or high-vol melt regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Look for breakout of previous 12 bars (excluding current bar)
        prev_highs = highs[-self.breakout_period - 1:-1]
        prev_lows = lows[-self.breakout_period - 1:-1]
        if len(prev_highs) < self.breakout_period or len(prev_lows) < self.breakout_period:
            return None

        breakout_high = max(prev_highs)
        breakout_low = min(prev_lows)
        curr_close = ctx.bar.close

        # Bullish release
        if curr_close > breakout_high:
            self.armed_coil = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "close": curr_close,
                    "breakout_high": breakout_high,
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "regime": regime,
                }
            )

        # Bearish release
        if curr_close < breakout_low:
            self.armed_coil = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "close": curr_close,
                    "breakout_low": breakout_low,
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "regime": regime,
                }
            )

        return None