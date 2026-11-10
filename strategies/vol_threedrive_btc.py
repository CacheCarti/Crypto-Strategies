from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityThreeDrive(Strategy):
    METADATA = {
        "name": "Volatility Three Drive",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours hold time
        "warmup_bars": 60,
        "required_features": [], # No external features needed for this pure price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar = -self.METADATA["warmup_bars"] # Ensure cooldown at start
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade

        # Parameters for detecting directional pushes
        self.momentum_period = 6 # Bars to check for momentum
        self.pullback_threshold_pct = 0.005 # 0.5% pullback to confirm a "drive"
        self.drive_strength_period = 3 # Bars for average candle body to check drive strength

    def _get_avg_candle_body(self, opens, closes, period):
        if len(opens) < period or len(closes) < period:
            return 0
        total_body = sum(abs(c - o) for c, o in zip(closes[-period:], opens[-period:]))
        return total_body / period

    def _is_strong_momentum(self, closes, period):
        if len(closes) < period:
            return False
        # Check if price has moved significantly in one direction
        return closes[-1] > closes[-period] * (1 + 0.01) or closes[-1] < closes[-period] * (1 - 0.01)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Check for existing position
        if ctx.has_position():
            # If in a position, just hold unless it's time to flat (handled by engine TP/SL)
            # Or if we want to reverse, the engine will handle it.
            return None

        # --- Long Setup: Three successive pushes down, then failure ---
        # Look for a pattern of (down, pullback, down, pullback, down, then failure to go lower)
        # Simplified for robustness and performance:
        # 1. Look for three consecutive bearish or very small body candles
        # 2. Followed by a strong bullish reversal candle
        # 3. Ensure overall trend is not extremely bullish (optional, but good for fading)

        # Simplified "Three Drive" approximation:
        # Look for 3-5 consecutive bearish candles (or small range) followed by a strong bullish candle
        # This implies exhaustion of selling pressure.

        lookback_bars = 5 # How many bars to look back for the "drives"
        if len(closes) < lookback_bars + 1:
            return None

        bearish_count = 0
        for i in range(1, lookback_bars + 1):
            if closes[-i] < opens[-i]: # Bearish candle
                bearish_count += 1
            elif abs(closes[-i] - opens[-i]) / opens[-i] < 0.001: # Very small body (doji-like)
                bearish_count += 0.5 # Partially counts as indecision/exhaustion

        # Current bar is a strong bullish reversal
        current_candle_body_pct = (current_price - opens[-1]) / opens[-1]
        
        # Long signal conditions
        if bearish_count >= 2.5 and current_candle_body_pct > 0.005: # At least 2.5 "bearish" bars and a strong bullish close
            # Ensure the current close is significantly higher than the previous close
            if current_price > closes[-2] * (1 + 0.003): # Current close is higher than 2 bars ago, confirming bounce
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.65,
                    metadata={
                        "reason": "three_drive_bearish_exhaustion_bullish_reversal",
                        "bearish_count": bearish_count,
                        "current_candle_body_pct": f"{current_candle_body_pct:.4f}",
                        "current_price": current_price,
                        "previous_close": closes[-2],
                        "regime": ctx.regime,
                    }
                )

        # --- Short Setup: Three successive pushes up, then failure ---
        # Look for 3-5 consecutive bullish or very small body candles
        # Followed by a strong bearish reversal candle
        # This implies exhaustion of buying pressure.

        bullish_count = 0
        for i in range(1, lookback_bars + 1):
            if closes[-i] > opens[-i]: # Bullish candle
                bullish_count += 1
            elif abs(closes[-i] - opens[-i]) / opens[-i] < 0.001: # Very small body (doji-like)
                bullish_count += 0.5 # Partially counts as indecision/exhaustion

        # Current bar is a strong bearish reversal
        current_candle_body_pct = (opens[-1] - current_price) / opens[-1]

        # Short signal conditions
        if bullish_count >= 2.5 and current_candle_body_pct > 0.005: # At least 2.5 "bullish" bars and a strong bearish close
            # Ensure the current close is significantly lower than the previous close
            if current_price < closes[-2] * (1 - 0.003): # Current close is lower than 2 bars ago, confirming drop
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.65,
                    metadata={
                        "reason": "three_drive_bullish_exhaustion_bearish_reversal",
                        "bullish_count": bullish_count,
                        "current_candle_body_pct": f"{current_candle_body_pct:.4f}",
                        "current_price": current_price,
                        "previous_close": closes[-2],
                        "regime": ctx.regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, so we can potentially re-enter sooner if conditions are met
        # self.last_signal_bar = -self.cooldown_bars # Or ctx.bar_index to start cooldown from close
        pass # The cooldown is applied from signal generation, not close, for this strategy.