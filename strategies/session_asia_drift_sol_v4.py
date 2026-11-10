from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolAsiaDrift(Strategy):
    METADATA = {
        "name": "SolAsiaDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for 2 hours, covering part of next session
        "warmup_bars": 50, # Need enough bars for median range and EMA
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"]
        self.cooldown_bars = 10 # Wait 10 bars after a trade before new entry
        self.asia_start_hour = 0
        self.asia_end_hour = 8
        self.median_range_period = 20 # For median range calculation
        self.move_threshold_multiplier = 1.5 # How many multiples of median range for a decisive move
        self.ema_period = 20 # For trend confirmation

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[-(period):]) / period # Simple average for initial EMA
        for v in values[len(values)-period:]: # Recalculate correctly
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Stand down in crisis or extreme fear/greed
        if crisis_score > 0.5 or fear_greed < 20 or fear_greed > 80:
            return None
        
        # Stand down if funding rate is extremely high (overheated market)
        if funding_rate > 0.0005: # 0.05% per 8h
            return None

        # Calculate median daily range for the last N days
        daily_ranges = []
        for i in range(1, self.median_range_period + 1):
            if len(highs) > i and len(lows) > i:
                daily_ranges.append(highs[-i] - lows[-i])
        
        if not daily_ranges:
            return None

        # Sort and find median
        sorted_ranges = sorted(daily_ranges)
        median_range = sorted_ranges[len(sorted_ranges) // 2]
        if median_range == 0:
            return None

        # Asia session logic: Detect decisive move
        if current_hour == self.asia_end_hour: # At the end of Asia session
            # Find the start of the Asia session (0 UTC)
            asia_session_bars = []
            for i in range(self.METADATA["warmup_bars"]):
                bar_hour = (ctx.bar.timestamp.hour - i) % 24
                if bar_hour >= self.asia_start_hour and bar_hour < self.asia_end_hour:
                    asia_session_bars.append(i)
                if bar_hour == (self.asia_start_hour - 1 + 24) % 24: # One bar before asia start
                    break
            
            if len(asia_session_bars) < 2: # Not enough bars in Asia session
                return None

            asia_open_price = closes[-asia_session_bars[-1]-1] # Open of the first bar of Asia session
            asia_close_price = closes[-1] # Close of the last bar of Asia session

            session_move = asia_close_price - asia_open_price
            move_magnitude = abs(session_move)

            # Skip single-bar spikes - ensure the move wasn't just the last bar
            if abs(closes[-1] - closes[-2]) / move_magnitude > 0.8 and move_magnitude > 0:
                return None # The move was mostly the last bar, potentially exhausted

            # Check if move is decisive enough
            if move_magnitude > median_range * self.move_threshold_multiplier:
                # Get EMA for trend confirmation
                ema_val = self._ema(closes, self.ema_period)
                if ema_val is None:
                    return None

                # Long signal
                if session_move > 0: # Bullish Asia session
                    if not ctx.has_position() and closes[-1] > ema_val and trend_regime in ["bull", "neutral"]:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "asia_session_drift_long",
                                "session_move_pct": session_move / asia_open_price * 100,
                                "median_range": median_range,
                                "ema": ema_val,
                                "current_price": closes[-1],
                                "trend_regime": trend_regime,
                                "fear_greed": fear_greed,
                            }
                        )

                # Short signal
                elif session_move < 0: # Bearish Asia session
                    if not ctx.has_position() and closes[-1] < ema_val and trend_regime in ["bear", "neutral"]:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "asia_session_drift_short",
                                "session_move_pct": session_move / asia_open_price * 100,
                                "median_range": median_range,
                                "ema": ema_val,
                                "current_price": closes[-1],
                                "trend_regime": trend_regime,
                                "fear_greed": fear_greed,
                            }
                        )

        # Exit logic: Flat before next Asia session starts or if trend changes
        if ctx.has_position() and current_hour == self.asia_start_hour - 1: # One hour before Asia starts again
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "exit_before_next_asia_session",
                    "current_price": closes[-1],
                    "hold_duration": ctx.bar_index - self.last_trade_bar,
                }
            )
        
        # Additional exit condition: Trend reversal
        if ctx.has_position():
            ema_val = self._ema(closes, self.ema_period)
            if ema_val is None:
                return None
            
            if ctx.position_direction() == "long" and closes[-1] < ema_val:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_long_ema_cross_down",
                        "current_price": closes[-1],
                        "ema": ema_val,
                        "hold_duration": ctx.bar_index - self.last_trade_bar,
                    }
                )
            elif ctx.position_direction() == "short" and closes[-1] > ema_val:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_short_ema_cross_up",
                        "current_price": closes[-1],
                        "ema": ema_val,
                        "hold_duration": ctx.bar_index - self.last_trade_bar,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add adaptive logic here based on position performance
        pass