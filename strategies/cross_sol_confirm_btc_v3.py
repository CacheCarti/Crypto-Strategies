from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetBTCLeaderFollower(Strategy):
    METADATA = {
        "name": "CrossAsset BTC Leader-Follower (SOL)",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars
        self.last_cross_direction = None # To detect transitions
        self.entry_bar_index = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.slow_ema_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate EMAs for BTC
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        current_btc_price = closes[-1]
        
        # Get cross-asset returns
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # BTC EMA Crossover detection
        current_cross_direction = None
        if fast_ema > slow_ema:
            current_cross_direction = "bullish"
        elif fast_ema < slow_ema:
            current_cross_direction = "bearish"

        # Trading logic
        if not ctx.has_position():
            signal_direction = None
            confidence = 0.0
            reason = ""
            
            # Long condition:
            # 1. BTC fast EMA crosses above slow EMA (transition)
            # 2. SOL return is positive and strong (leader confirms trend)
            # 3. BTC return is positive (target confirms)
            if current_cross_direction == "bullish" and self.last_cross_direction == "bearish" and \
               sol_return_pct > 0.5 and btc_return_pct > 0.1: # Thresholds for fresh positive returns
                
                signal_direction = "long"
                confidence = min(1.0, 0.5 + (sol_return_pct / 2.0) + (btc_return_pct / 1.0)) # Scale confidence
                reason = "btc_ema_bull_cross_sol_btc_positive_returns"
                
            # Short condition:
            # 1. BTC fast EMA crosses below slow EMA (transition)
            # 2. SOL return is negative and strong (leader confirms trend)
            # 3. BTC return is negative (target confirms)
            elif current_cross_direction == "bearish" and self.last_cross_direction == "bullish" and \
                 sol_return_pct < -0.5 and btc_return_pct < -0.1: # Thresholds for fresh negative returns
                
                signal_direction = "short"
                confidence = min(1.0, 0.5 + (abs(sol_return_pct) / 2.0) + (abs(btc_return_pct) / 1.0)) # Scale confidence
                reason = "btc_ema_bear_cross_sol_btc_negative_returns"

            if signal_direction:
                self.last_signal_bar = ctx.bar_index
                self.entry_bar_index = ctx.bar_index
                self.last_cross_direction = current_cross_direction # Update last cross direction after signal
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "btc_price": current_btc_price,
                        "sol_return_pct": sol_return_pct,
                        "btc_return_pct": btc_return_pct,
                    }
                )
        else: # If a position is open
            # Exit if SOL returns normalize (cross 0) or BTC returns turn against us
            # Or if the initial cross direction reverses
            
            position_direction = ctx.position_direction()
            exit_signal = False
            exit_reason = ""

            if position_direction == "long":
                # Exit if SOL turns negative, or BTC turns negative, or bullish cross reverses
                if sol_return_pct < -0.1 or btc_return_pct < -0.1 or current_cross_direction == "bearish":
                    exit_signal = True
                    exit_reason = "long_exit_sol_btc_returns_or_ema_reverse"
            elif position_direction == "short":
                # Exit if SOL turns positive, or BTC turns positive, or bearish cross reverses
                if sol_return_pct > 0.1 or btc_return_pct > 0.1 or current_cross_direction == "bullish":
                    exit_signal = True
                    exit_reason = "short_exit_sol_btc_returns_or_ema_reverse"

            if exit_signal:
                self.last_signal_bar = ctx.bar_index
                self.last_cross_direction = current_cross_direction # Update last cross direction on exit too
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": exit_reason,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "btc_price": current_btc_price,
                    "sol_return_pct": sol_return_pct,
                    "btc_return_pct": btc_return_pct,
                    "bars_held": ctx.bar_index - self.entry_bar_index
                })
        
        # Update last_cross_direction even if no signal to detect next transition
        self.last_cross_direction = current_cross_direction

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis, reset state if needed
        pass