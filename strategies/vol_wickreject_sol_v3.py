from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolWickRejectSol(Strategy):
    METADATA = {
        "name": "Vol Wick Reject Sol",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 30,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10 # Wait 10 bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars
        self.wick_threshold_pct = 0.015 # 1.5% wick relative to body
        self.volume_ratio_threshold = 1.5 # Volume must be 1.5x average
        self.body_ratio_threshold = 0.25 # Body must be at least 25% of total bar range
        self.atr_period = 14
        self.sma_volume_period = 20

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 2) # Need current, prev, and lookback for ATR/SMA
        highs = ctx.highs(self.warmup_bars + 2)
        lows = ctx.lows(self.warmup_bars + 2)
        opens = ctx.opens(self.warmup_bars + 2)
        volumes = ctx.volumes(self.warmup_bars + 2)

        if len(closes) < self.warmup_bars + 2:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        current_volume = volumes[-1]

        prev_close = closes[-2]
        prev_open = opens[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        # Calculate average volume
        avg_volume = self._sma(volumes[:-1], self.sma_volume_period) # Exclude current bar for avg
        if avg_volume is None or avg_volume == 0:
            return None

        # Calculate ATR for dynamic wick threshold
        atr_val = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period) # ATR based on previous bars
        if atr_val is None or atr_val == 0:
            return None

        # --- Rejection Wick Logic ---
        # Bar -2 (prev_prev) is the potential large wick bar
        # Bar -1 (prev) is the confirmation bar
        # Bar 0 (current) is the signal bar

        # Check previous bar for large wick and high volume
        prev_bar_range = prev_high - prev_low
        prev_bar_body_size = abs(prev_close - prev_open)

        if prev_bar_range == 0: # Avoid division by zero
            return None

        # Ensure a significant body exists
        if prev_bar_body_size / prev_bar_range < self.body_ratio_threshold:
            return None

        # Dynamic wick threshold based on ATR
        min_wick_size = atr_val * self.wick_threshold_pct * 100 # Adjust to be a fraction of ATR

        # Long Entry: Bullish Rejection (Hammer/Dragonfly Doji-like)
        # Large lower wick on prev_bar, prev_bar closes near its high
        if prev_close > prev_open: # Green candle
            lower_wick = prev_open - prev_low
            upper_wick = prev_high - prev_close
            is_large_lower_wick = lower_wick > min_wick_size and lower_wick > upper_wick
            is_prev_high_volume = prev_volume > avg_volume * self.volume_ratio_threshold

            if is_large_lower_wick and is_prev_high_volume:
                # Confirmation: current bar closes above prev_bar's close, confirming rejection
                if current_close > prev_close:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "bullish_wick_rejection_confirmation",
                            "prev_lower_wick_size": lower_wick,
                            "prev_volume_ratio": prev_volume / avg_volume,
                            "current_close": current_close,
                            "prev_close": prev_close,
                            "atr": atr_val
                        }
                    )

        # Short Entry: Bearish Rejection (Shooting Star/Gravestone Doji-like)
        # Large upper wick on prev_bar, prev_bar closes near its low
        elif prev_close < prev_open: # Red candle
            upper_wick = prev_high - prev_open
            lower_wick = prev_close - prev_low
            is_large_upper_wick = upper_wick > min_wick_size and upper_wick > lower_wick
            is_prev_high_volume = prev_volume > avg_volume * self.volume_ratio_threshold

            if is_large_upper_wick and is_prev_high_volume:
                # Confirmation: current bar closes below prev_bar's close, confirming rejection
                if current_close < prev_close:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "bearish_wick_rejection_confirmation",
                            "prev_upper_wick_size": upper_wick,
                            "prev_volume_ratio": prev_volume / avg_volume,
                            "current_close": current_close,
                            "prev_close": prev_close,
                            "atr": atr_val
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this exploration, just reset cooldown
        pass