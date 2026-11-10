from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolWickRejectEth(Strategy):
    METADATA = {
        "name": "Vol Wick Reject ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.bars_since_last_trade = self.cooldown_bars + 1 # Allow trading immediately
        self.wick_ratio_threshold = 0.6 # Ratio of wick to total bar range
        self.volume_multiplier = 1.5 # Volume compared to average
        self.rejection_confirm_threshold = 0.5 # How much the confirming bar closes into the rejection wick

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_trade += 1

        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if self.bars_since_last_trade <= self.cooldown_bars:
            if ctx.has_position():
                return None # Hold position during cooldown
            else:
                return None # Skip new entries during cooldown

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume

        # Calculate average volume
        avg_volume = self._sma(volumes[:-1], 20) # Average of previous 20 bars
        if avg_volume is None:
            return None

        # Check for existing position to manage exits
        if ctx.has_position():
            # No specific exit logic beyond declared SL/TP for this strategy,
            # but could add conditions here later if needed.
            return None

        # --- Bearish Rejection Wick (Potential Short) ---
        # Bar N-1 (previous bar) shows a large upper wick
        prev_close = closes[-2]
        prev_open = opens[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        prev_bar_range = prev_high - prev_low
        if prev_bar_range > 0:
            upper_wick = prev_high - max(prev_open, prev_close)
            lower_wick = min(prev_open, prev_close) - prev_low

            # Condition 1: Large upper wick
            is_large_upper_wick = (upper_wick / prev_bar_range) >= self.wick_ratio_threshold

            # Condition 2: High volume on the wick bar
            is_high_volume_prev = (prev_volume > avg_volume * self.volume_multiplier)

            # Condition 3: Current bar (N) confirms rejection by closing significantly lower
            # and closing into the previous bar's body/lower wick
            is_confirming_bearish = (
                current_close < prev_close and
                current_close < min(prev_open, prev_close) + (max(prev_open, prev_close) - min(prev_open, prev_close)) * (1 - self.rejection_confirm_threshold)
            )

            if is_large_upper_wick and is_high_volume_prev and is_confirming_bearish:
                self.bars_since_last_trade = 0
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "bearish_vol_wick_rejection_confirmed",
                        "prev_bar_upper_wick_ratio": round(upper_wick / prev_bar_range, 2),
                        "prev_bar_volume_mult": round(prev_volume / avg_volume, 2),
                        "current_close": current_close,
                        "prev_close": prev_close,
                        "regime": ctx.market.get("regime", "NORMAL"),
                        "crisis_score": round(ctx.market.get("crisis_score", 0.0), 2),
                    }
                )

        # --- Bullish Rejection Wick (Potential Long) ---
        # Bar N-1 (previous bar) shows a large lower wick
        if prev_bar_range > 0:
            upper_wick = prev_high - max(prev_open, prev_close)
            lower_wick = min(prev_open, prev_close) - prev_low

            # Condition 1: Large lower wick
            is_large_lower_wick = (lower_wick / prev_bar_range) >= self.wick_ratio_threshold

            # Condition 2: High volume on the wick bar (same as above)
            # is_high_volume_prev is already calculated

            # Condition 3: Current bar (N) confirms rejection by closing significantly higher
            # and closing into the previous bar's body/upper wick
            is_confirming_bullish = (
                current_close > prev_close and
                current_close > max(prev_open, prev_close) - (max(prev_open, prev_close) - min(prev_open, prev_close)) * (1 - self.rejection_confirm_threshold)
            )

            if is_large_lower_wick and is_high_volume_prev and is_confirming_bullish:
                self.bars_since_last_trade = 0
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "bullish_vol_wick_rejection_confirmed",
                        "prev_bar_lower_wick_ratio": round(lower_wick / prev_bar_range, 2),
                        "prev_bar_volume_mult": round(prev_volume / avg_volume, 2),
                        "current_close": current_close,
                        "prev_close": prev_close,
                        "regime": ctx.market.get("regime", "NORMAL"),
                        "crisis_score": round(ctx.market.get("crisis_score", 0.0), 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, allowing a new trade after cooldown_bars
        self.bars_since_last_trade = 0
        pass # No adaptive logic for this strategy yet