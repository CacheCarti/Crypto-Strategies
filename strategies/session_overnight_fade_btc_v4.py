from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightFadeBTC(Strategy):
    METADATA = {
        "name": "Overnight Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For volatility and trend assessment
        self.entry_cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.entry_cooldown_bars

        # Store session boundaries
        self.session_start_hour = 0  # UTC Midnight
        self.session_end_hour = 6    # UTC 6 AM
        self.session_midpoint_hour = 3 # UTC 3 AM

        self.min_stretch_std = 1.0 # Minimum stretch for fading
        self.max_volatility_std_dev = 2.0 # Max std dev from avg vol to fade
        self.min_confidence = 0.6
        self.max_fear_greed = 70 # Don't fade into extreme greed
        self.min_fear_greed = 30 # Don't fade into extreme fear
        self.max_funding_rate = 0.0003 # Don't fade against very high funding

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        volumes = ctx.volumes(self.lookback_period + 1)

        if None in (closes, highs, lows, volumes):
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # --- Regime Filters ---
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        if fear_greed > self.max_fear_greed or fear_greed < self.min_fear_greed:
            return None # Avoid extreme sentiment
        if funding_rate > self.max_funding_rate:
            return None # Avoid strong bullish funding bias
        if regime in ["CRISIS", "MELTDOWN"]:
            return None # Stand down in crisis

        # --- Session Logic ---
        # Only consider entry between session_start_hour and session_midpoint_hour
        # and only if we don't have a position
        if not ctx.has_position() and self.session_start_hour <= current_hour < self.session_midpoint_hour:
            # Check for "unusually stretched" move
            session_high = max(highs[-self.lookback_period:])
            session_low = min(lows[-self.lookback_period:])
            session_range = session_high - session_low
            
            if session_range == 0:
                return None

            # Calculate current price's position within the session range
            # We're looking to fade if price is near an extreme of the session
            stretch_up = (current_close - session_low) / session_range
            stretch_down = (session_high - current_close) / session_range

            # Calculate historical volatility to gauge "unusually stretched"
            # Using standard deviation of returns
            returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
            avg_volatility = self._stddev(returns, self.lookback_period)
            
            if avg_volatility is None or avg_volatility == 0:
                return None

            # Calculate current bar's volatility (simple range for current bar)
            current_bar_vol = math.log(ctx.bar.high / ctx.bar.low) if ctx.bar.low > 0 else 0
            
            # This is a proxy for "stalling" - if current bar vol is low relative to average
            is_stalling = (current_bar_vol < avg_volatility * 0.5) if avg_volatility > 0 else False


            # Long entry: Price stretched low, but not in a strong bear trend, and showing signs of stalling
            if stretch_up < 0.2 and trend_regime != "bear" and is_stalling: # Price is in the bottom 20% of the session range
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=self.min_confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_fade_long_stretch_low",
                        "current_hour": current_hour,
                        "stretch_up_ratio": stretch_up,
                        "session_range": session_range,
                        "current_close": current_close,
                        "trend_regime": trend_regime,
                        "is_stalling": is_stalling,
                        "fear_greed": fear_greed,
                        "funding_rate": funding_rate
                    }
                )

            # Short entry: Price stretched high, but not in a strong bull trend, and showing signs of stalling
            elif stretch_down < 0.2 and trend_regime != "bull" and is_stalling: # Price is in the top 20% of the session range
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=self.min_confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_fade_short_stretch_high",
                        "current_hour": current_hour,
                        "stretch_down_ratio": stretch_down,
                        "session_range": session_range,
                        "current_close": current_close,
                        "trend_regime": trend_regime,
                        "is_stalling": is_stalling,
                        "fear_greed": fear_greed,
                        "funding_rate": funding_rate
                    }
                )

        # --- Exit Logic ---
        # If we have a position and it's approaching the next US session (e.g., 10 AM UTC)
        # or end of our intended session window (6 AM UTC), consider exiting
        if ctx.has_position():
            # Exit before the start of the next major session (e.g., US session opens around 13-14 UTC)
            # Or at the end of our declared hold time, which is 6 AM UTC if we entered early in the session
            # For simplicity, let's target exiting around 10 AM UTC (hour 10)
            if current_hour >= 10: # Exit before US session gets fully underway
                 return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "session_end_time_exit",
                        "current_hour": current_hour,
                        "entry_time": ctx.position["entry_bar_timestamp"].hour if ctx.position else "N/A"
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar on position close to ensure cooldown
        self.last_trade_bar = ctx.bar_index
        # Optional: Adaptive logic based on position return
        # if position["return_bps"] > 0:
        #     self.min_confidence = min(1.0, self.min_confidence * 1.05)
        # else:
        #     self.min_confidence = max(0.5, self.min_confidence * 0.95)