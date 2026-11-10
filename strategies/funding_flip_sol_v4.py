from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipSol(Strategy):
    METADATA = {
        "name": "Funding Flip SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # 4 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 8 # Look back for funding rate stability
        self.price_confirm_bars = 3 # Bars to confirm price action after funding flip
        self.cooldown_bars = 10 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.ema_period = 20 # For price confirmation
        self.funding_history = [] # To store recent funding rates

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + self.price_confirm_bars)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_solusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.funding_period:
            self.funding_history.pop(0)

        if len(self.funding_history) < self.funding_period:
            return None

        # Check for funding rate stability and flip
        prev_funding_slice = self.funding_history[:-1]
        if not prev_funding_slice:
            return None

        # All previous funding rates must be consistently positive or negative
        # And the current funding rate must have flipped sign
        previous_signs_positive = all(f > 0 for f in prev_funding_slice)
        previous_signs_negative = all(f < 0 for f in prev_funding_slice)

        signal_direction = None
        reason = None

        # Funding flip from positive to negative (short signal)
        if previous_signs_positive and current_funding < 0:
            signal_direction = "short"
            reason = "funding_flip_pos_to_neg"
        # Funding flip from negative to positive (long signal)
        elif previous_signs_negative and current_funding > 0:
            signal_direction = "long"
            reason = "funding_flip_neg_to_pos"

        if signal_direction:
            # Regime filter: Only trade in normal or volatile regimes, not crisis or meltdown
            # And only if trend is not strongly against us
            market_regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")
            
            if market_regime in ["CRISIS", "MELTDOWN"]:
                return None
            
            if signal_direction == "long" and trend_regime == "bear":
                return None
            if signal_direction == "short" and trend_regime == "bull":
                return None

            # Price confirmation:
            # For long, price should be above EMA or showing upward momentum in last few bars
            # For short, price should be below EMA or showing downward momentum in last few bars
            ema_val = self._ema(closes, self.ema_period)
            if ema_val is None:
                return None

            price_confirms = False
            if signal_direction == "long":
                # Price should be above EMA, or at least showing recent positive movement
                if closes[-1] > ema_val and closes[-1] > closes[-2]:
                    price_confirms = True
                elif closes[-1] > closes[-2] and closes[-2] > closes[-3]: # Basic momentum
                    price_confirms = True
            elif signal_direction == "short":
                # Price should be below EMA, or at least showing recent negative movement
                if closes[-1] < ema_val and closes[-1] < closes[-2]:
                    price_confirms = True
                elif closes[-1] < closes[-2] and closes[-2] < closes[-3]: # Basic momentum
                    price_confirms = True

            if not price_confirms:
                return None # Price did not confirm the funding flip

            # Confidence based on trend strength
            confidence = 0.6
            trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
            if trend_confidence > 0.5:
                if (signal_direction == "long" and trend_regime == "bull") or \
                   (signal_direction == "short" and trend_regime == "bear"):
                    confidence += 0.2
                else:
                    confidence -= 0.1 # Slightly reduce if trend is neutral but not opposing

            if ctx.has_position():
                if ctx.position_direction() != signal_direction:
                    # Flip position if opposite signal appears
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=min(1.0, confidence),
                        metadata={
                            "reason": f"flip_position_{reason}",
                            "current_funding": current_funding,
                            "prev_funding_avg": sum(prev_funding_slice) / len(prev_funding_slice),
                            "ema_val": ema_val,
                            "current_price": closes[-1],
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )
                return None # Already in position, don't re-signal
            else:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=min(1.0, confidence),
                    metadata={
                        "reason": reason,
                        "current_funding": current_funding,
                        "prev_funding_avg": sum(prev_funding_slice) / len(prev_funding_slice),
                        "ema_val": ema_val,
                        "current_price": closes[-1],
                        "market_regime": market_regime,
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for now, just logging or resetting state if needed
        pass