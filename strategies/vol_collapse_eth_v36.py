from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolCoilRelease(Strategy):
    METADATA = {
        "name": "EthVolCoilRelease",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.channel_period = 12
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.spike_seen = False
        self.in_compression = False
        self.compression_timeout_bars = 36
        self.compression_start_bar = -999

    def _atr_series(self, highs, lows, closes, length):
        trs = []
        for i in range(1, length + 1):
            idx = -i
            prev_idx = idx - 1
            tr = max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[prev_idx]),
                abs(lows[idx] - closes[prev_idx])
            )
            trs.append(tr)
        return trs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.spike_seen = False
        self.in_compression = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + 20)
        highs = ctx.highs(self.median_period + 20)
        lows = ctx.lows(self.median_period + 20)

        if len(closes) < self.median_period + 5:
            return None

        # Calculate True Range for past 168 bars to compute current ATR vs median historical TR
        # Fast local volatility: 5-bar ATR
        trs_all = self._atr_series(highs, lows, closes, self.median_period)
        fast_tr = sum(trs_all[:5]) / 5.0
        sorted_trs = sorted(trs_all)
        median_tr = sorted_trs[len(sorted_trs) // 2]

        if median_tr <= 0:
            return None

        vol_ratio = fast_tr / median_tr

        # State tracking: Spike occurred when ratio > 1.8x
        if vol_ratio > 1.8:
            self.spike_seen = True

        # Transition into compression: Spike was seen, but now vol collapsed below 1.05x median
        if self.spike_seen and vol_ratio < 1.05:
            self.spike_seen = False
            self.in_compression = True
            self.compression_start_bar = ctx.bar_index

        # Age out compression state if no breakout occurs within window
        if self.in_compression and (ctx.bar_index - self.compression_start_bar > self.compression_timeout_bars):
            self.in_compression = False

        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        if not self.in_compression:
            return None

        # Look for breakout of 12-bar high/low (prior to current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        if len(prior_highs) < self.channel_period:
            return None

        break_high = max(prior_highs)
        break_low = min(prior_lows)
        curr_close = ctx.bar.close

        # Coil release triggers
        if curr_close > break_high:
            self.in_compression = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                metadata={
                    "reason": "vol_collapse_coil_breakout_long",
                    "vol_ratio": round(vol_ratio, 3),
                    "break_high": round(break_high, 2),
                    "close": round(curr_close, 2)
                }
            )

        if curr_close < break_low:
            self.in_compression = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                metadata={
                    "reason": "vol_collapse_coil_breakout_short",
                    "vol_ratio": round(vol_ratio, 3),
                    "break_low": round(break_low, 2),
                    "close": round(curr_close, 2)
                }
            )

        return None