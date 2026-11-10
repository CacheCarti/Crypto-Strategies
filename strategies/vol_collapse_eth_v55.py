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
        "warmup_bars": 175,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.breakout_period = 12
        self.atr_period = 14
        self.cooldown_bars = 8
        self.max_compression_window = 24
        
        self.spike_detected = False
        self.compressed = False
        self.compression_bar = -100
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + 5)
        highs = ctx.highs(self.median_period + 5)
        lows = ctx.lows(self.median_period + 5)

        if len(closes) < self.median_period + 2:
            return None

        # Calculate Parkinson or True Range realized volatility proxy per bar
        # Using 5-bar rolling ATR normalized by close price as short-term volatility
        ranges = []
        for i in range(len(closes) - self.median_period, len(closes)):
            h = highs[i]
            l = lows[i]
            c = closes[i]
            ranges.append((h - l) / c if c > 0 else 0.0)

        current_vol = sum(ranges[-5:]) / 5.0
        sorted_ranges = sorted(ranges)
        median_vol = sorted_ranges[len(sorted_ranges) // 2]

        if median_vol <= 0:
            return None

        # State machine: detect spike (>2.0x median), then collapse back below 1.0x median
        if current_vol > 2.0 * median_vol:
            self.spike_detected = True

        if self.spike_detected and current_vol < 1.0 * median_vol:
            self.spike_detected = False
            self.compressed = True
            self.compression_bar = ctx.bar_index

        # Expire compression setup if breakout doesn't occur within allowed window
        if self.compressed and (ctx.bar_index - self.compression_bar > self.max_compression_window):
            self.compressed = False

        if ctx.has_position():
            return None

        # Cooldown guard to avoid churn and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Only trigger breakout entries while compressed state is active
        if not self.compressed:
            return None

        current_close = ctx.bar.close
        recent_highs = highs[-(self.breakout_period + 1):-1]
        recent_lows = lows[-(self.breakout_period + 1):-1]

        if len(recent_highs) < self.breakout_period:
            return None

        breakout_high = max(recent_highs)
        breakout_low = min(recent_lows)

        # Require a clear transition breakout
        if current_close > breakout_high:
            self.compressed = False
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "breakout_high": round(breakout_high, 2),
                    "close": round(current_close, 2)
                }
            )

        if current_close < breakout_low:
            self.compressed = False
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "breakout_low": round(breakout_low, 2),
                    "close": round(current_close, 2)
                }
            )

        return None