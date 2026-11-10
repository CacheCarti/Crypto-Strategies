from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityEventFade(Strategy):
    METADATA = {
        "name": "VolatilityEventFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5
        self.continuation_fail_period = 3
        self.cooldown_bars = 10
        self.last_signal_bar_index = -self.cooldown_bars - 1 # Ensure immediate trading on warmup completion

        # Parameters for volatility detection and confirmation
        self.vol_period = 20
        self.vol_multiplier = 1.5 # How many multiples of average true range for a significant move
        self.min_regime_confidence = 0.6 # Only trade if market regime is "calm" or "normal" with confidence

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_signal_bar_index + self.cooldown_bars:
            return None

        # --- Volatility and Continuation Logic ---
        # Detect a strong streak (e.g., 5 consecutive bars closing in the same direction)
        # Then, identify a failure of continuation (e.g., 3 bars after the streak that fail to continue the direction)

        if len(closes) < self.streak_period + self.continuation_fail_period + 1:
            return None

        # Check for bullish streak
        is_bull_streak = True
        for i in range(self.streak_period):
            if closes[-(self.streak_period + self.continuation_fail_period + 1 - i)] <= closes[-(self.streak_period + self.continuation_fail_period + 2 - i)]:
                is_bull_streak = False
                break

        # Check for bearish streak
        is_bear_streak = True
        for i in range(self.streak_period):
            if closes[-(self.streak_period + self.continuation_fail_period + 1 - i)] >= closes[-(self.streak_period + self.continuation_fail_period + 2 - i)]:
                is_bear_streak = False
                break

        # Check for failed continuation after a streak
        signal_direction = None
        reason = ""
        entry_price = ctx.bar.close

        atr_val = self._atr(highs, lows, closes, self.vol_period)
        if atr_val is None:
            return None
        
        # We only consider fading if regime is not too volatile
        # Using ctx.market.get("regime") is fine here, it's not a required_feature
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime not in ["NORMAL", "CALM"]:
             return None

        if is_bull_streak:
            # Failed bullish continuation: price drops significantly after the streak
            # Check if the last 'continuation_fail_period' bars show reversal
            # For a bullish streak, we look for a significant drop to fade (short)
            if all(closes[-(self.continuation_fail_period - i)] < closes[-(self.continuation_fail_period - i + 1)] for i in range(self.continuation_fail_period)):
                # Additionally, check if the reversal move is significant relative to ATR
                move_since_streak_peak = closes[-(self.continuation_fail_period + 1)] - ctx.bar.close
                if move_since_streak_peak > self.vol_multiplier * atr_val:
                    signal_direction = "short"
                    reason = "fade_bull_streak_failed_continuation"

        elif is_bear_streak:
            # Failed bearish continuation: price rises significantly after the streak
            # For a bearish streak, we look for a significant rise to fade (long)
            if all(closes[-(self.continuation_fail_period - i)] > closes[-(self.continuation_fail_period - i + 1)] for i in range(self.continuation_fail_period)):
                # Additionally, check if the reversal move is significant relative to ATR
                move_since_streak_trough = ctx.bar.close - closes[-(self.continuation_fail_period + 1)]
                if move_since_streak_trough > self.vol_multiplier * atr_val:
                    signal_direction = "long"
                    reason = "fade_bear_streak_failed_continuation"
        
        if ctx.has_position():
            if signal_direction == ctx.position_direction():
                return ctx.signal("hold")
            elif signal_direction is not None and signal_direction != ctx.position_direction():
                # Flip position if opposite signal is strong
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=0.8,
                    metadata={
                        "reason": f"flip_{reason}",
                        "atr": atr_val,
                        "current_price": entry_price,
                        "market_regime": market_regime,
                    }
                )
            else:
                # No new signal, but holding a position
                return ctx.signal("hold")

        if signal_direction:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=0.7, # Moderate confidence for this pattern
                metadata={
                    "reason": reason,
                    "atr": atr_val,
                    "current_price": entry_price,
                    "market_regime": market_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for debugging or adaptive logic
        pass