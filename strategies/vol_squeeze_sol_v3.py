from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class VolatilityBreakout(Strategy):
    METADATA = {
        "name": "VolatilityBreakoutSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 100,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.compression_lookback = 60 # Lookback for ATR quartile
        self.breakout_multiplier = 1.0 # Multiplier for ATR to define breakout
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store recent ATR values for quartile calculation
        self.atr_history = collections.deque(maxlen=self.compression_lookback)
        self.is_compressed = False

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Simple moving average of TR
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Update ATR history
        self.atr_history.append(current_atr)

        # Ensure enough data for compression check
        if len(self.atr_history) < self.compression_lookback:
            return None

        # Calculate quartiles for ATR history
        sorted_atr_history = sorted(list(self.atr_history))
        q1_index = math.floor(len(sorted_atr_history) * 0.25)
        q1_atr = sorted_atr_history[q1_index]

        # Check for compression
        if current_atr < q1_atr and not self.is_compressed:
            self.is_compressed = True
            # print(f"Bar {ctx.bar_index}: Compression detected. Current ATR: {current_atr:.4f}, Q1 ATR: {q1_atr:.4f}")
            return None # Wait for breakout after compression

        # Check if compression has ended
        if current_atr >= q1_atr and self.is_compressed:
            self.is_compressed = False
            # print(f"Bar {ctx.bar_index}: Compression ended. Current ATR: {current_atr:.4f}, Q1 ATR: {q1_atr:.4f}")

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: breakout after compression
        if not ctx.has_position() and self.is_compressed:
            # Check for a breakout from the recent high/low
            # Use the previous bar's range as a reference for breakout
            prev_high = ctx.highs(2)[0]
            prev_low = ctx.lows(2)[0]
            current_close = ctx.bar.close
            current_open = ctx.bar.open

            # A breakout is defined as closing significantly outside the previous bar's range
            # We use a multiple of the current ATR to define "significantly"
            breakout_threshold = current_atr * self.breakout_multiplier

            long_condition = current_close > prev_high + breakout_threshold
            short_condition = current_close < prev_low - breakout_threshold

            if long_condition:
                self.last_trade_bar = ctx.bar_index
                self.is_compressed = False # Reset compression state after breakout
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "vol_compression_long_breakout",
                        "current_atr": current_atr,
                        "q1_atr": q1_atr,
                        "breakout_threshold": breakout_threshold,
                        "current_close": current_close,
                        "prev_high": prev_high,
                    }
                )

            if short_condition:
                self.last_trade_bar = ctx.bar_index
                self.is_compressed = False # Reset compression state after breakout
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "vol_compression_short_breakout",
                        "current_atr": current_atr,
                        "q1_atr": q1_atr,
                        "breakout_threshold": breakout_threshold,
                        "current_close": current_close,
                        "prev_low": prev_low,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset compression state and last trade bar on position close
        # This ensures we don't immediately re-enter and respect cooldown
        self.is_compressed = False
        self.last_trade_bar = ctx.bar_index
        # You could adapt parameters here based on position.return_bps, e.g.,
        # if position["return_bps"] < 0:
        #     self.breakout_multiplier *= 0.95 # Reduce multiplier after loss
        # else:
        #     self.breakout_multiplier *= 1.05 # Increase after win, with a cap
        # self.breakout_multiplier = max(0.5, min(2.0, self.breakout_multiplier))
        pass