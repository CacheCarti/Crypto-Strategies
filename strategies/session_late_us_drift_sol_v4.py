from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionLateUsDriftSol(Strategy):
    METADATA = {
        "name": "SessionLateUsDriftSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # 4 hours intended hold
        "warmup_bars": 50,
        "required_features": [], # No explicit features used, only OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20  # For median range calculation
        self.cooldown_bars = 4    # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars
        self.session_started_bar = -1 # Track when the session started for one-entry-per-session logic

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: If currently holding and it's near UTC 0, flatten.
        if ctx.has_position() and current_hour == 23: # Exit before UTC 0
            # Ensure we only signal flat once per session end
            if ctx.bar_index != self.session_started_bar: # Avoid flattening on the same bar we entered
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Regime filter: Only trade in normal or volatile regimes, and neutral/bullish trend
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "HIGH_VOL"]:
            return None
        
        # Trend filter: Only trade if trend is not strongly bearish
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        if trend_regime == "bear" and trend_confidence > 0.6:
            return None

        # Entry logic: Late US session (UTC 18-22)
        if 18 <= current_hour <= 22:
            # Only one entry per session
            if self.session_started_bar == ctx.bar_index - 1: # Already entered in the current session
                return None
            
            median_range = self._median_range(highs[:-1], lows[:-1], self.lookback_period) # Median range of previous bars
            if median_range is None:
                return None

            # Look for a decisive move in the current bar
            bar_range = ctx.bar.high - ctx.bar.low
            bar_move = ctx.bar.close - ctx.bar.open

            # Avoid single-bar spikes: ensure bar range is reasonable but not extreme
            if bar_range > median_range * 2.0: # Too spiky, likely a wick
                return None

            # Decisive move check: current bar's move must be significant relative to median range
            # and not already exhausted (i.e., not a tiny body after a big move)
            if abs(bar_move) > median_range * 0.5: # At least half of median range
                
                # Check for strong bullish move
                if bar_move > 0 and ctx.bar.close > ctx.bar.open: # Bullish bar
                    # Ensure price is not at the very top of its recent range (exhaustion)
                    if ctx.bar.close < max(highs[-self.lookback_period:]) * 0.99: # Not near recent high
                        self.last_signal_bar = ctx.bar_index
                        self.session_started_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "session_late_us_drift_long",
                                "current_hour": current_hour,
                                "bar_move": round(bar_move, 2),
                                "median_range": round(median_range, 2),
                                "trend_regime": trend_regime,
                            }
                        )

                # Check for strong bearish move
                elif bar_move < 0 and ctx.bar.close < ctx.bar.open: # Bearish bar
                    # Ensure price is not at the very bottom of its recent range (exhaustion)
                    if ctx.bar.close > min(lows[-self.lookback_period:]) * 1.01: # Not near recent low
                        self.last_signal_bar = ctx.bar_index
                        self.session_started_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "session_late_us_drift_short",
                                "current_hour": current_hour,
                                "bar_move": round(bar_move, 2),
                                "median_range": round(median_range, 2),
                                "trend_regime": trend_regime,
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session_started_bar if position closed, allowing new entries after cooldown
        # This prevents a bug where self.session_started_bar could prevent entry for subsequent days
        # if the position was held across session boundaries.
        self.session_started_bar = -1 # Reset for next session.
        pass