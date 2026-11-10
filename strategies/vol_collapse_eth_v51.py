from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 168
        self.channel_len = 12
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.was_spiked = False
        self.coiled = False

    def _atr_series(self, highs, lows, closes, period=14):
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return []
        atrs = []
        curr = sum(trs[:period]) / period
        atrs.append(curr)
        for val in trs[period:]:
            curr = (curr * (period - 1) + val) / period
            atrs.append(curr)
        return atrs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False
        self.was_spiked = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_window + 20)
        highs = ctx.highs(self.vol_window + 20)
        lows = ctx.lows(self.vol_window + 20)

        if len(closes) < self.vol_window + 15:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos.get("entry_price", ctx.bar.close)
            direction = pos.get("direction", "long")
            
            # Simple trailing / mean reversion exit using 6-period SMA
            sma6 = sum(closes[-6:]) / 6.0
            if direction == "long" and ctx.bar.close < sma6 and ctx.bar.close > entry_price:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "profit_lock_sma6_cross"})
            elif direction == "short" and ctx.bar.close > sma6 and ctx.bar.close < entry_price:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "profit_lock_sma6_cross"})
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute ATR history to track volatility spike and collapse
        atrs = self._atr_series(highs, lows, closes, period=14)
        if len(atrs) < self.vol_window:
            return None

        recent_atrs = atrs[-self.vol_window:]
        current_atr = recent_atrs[-1]
        sorted_atrs = sorted(recent_atrs)
        median_atr = sorted_atrs[len(sorted_atrs) // 2]

        if median_atr <= 0:
            return None

        vol_ratio = current_atr / median_atr

        # Track state transitions: spike (> 1.8x median) -> collapse (< 1.1x median)
        max_recent_vol = max(recent_atrs[-36:]) / median_atr
        if max_recent_vol >= 1.8:
            self.was_spiked = True

        if self.was_spiked and vol_ratio < 1.1:
            self.coiled = True

        # Coil state invalidates if volatility drifts too high again without triggering
        if vol_ratio > 1.4:
            self.coiled = False
            self.was_spiked = False

        if not self.coiled:
            return None

        # Donchian coil release trigger (12-bar breakout excluding current bar)
        recent_highs = highs[-self.channel_len - 1:-1]
        recent_lows = lows[-self.channel_len - 1:-1]
        upper_barrier = max(recent_highs)
        lower_barrier = min(recent_lows)

        current_close = ctx.bar.close

        if current_close > upper_barrier:
            self.coiled = False
            self.was_spiked = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "upper_barrier": upper_barrier,
                    "close": current_close,
                }
            )

        if current_close < lower_barrier:
            self.coiled = False
            self.was_spiked = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "lower_barrier": lower_barrier,
                    "close": current_close,
                }
            )

        return None