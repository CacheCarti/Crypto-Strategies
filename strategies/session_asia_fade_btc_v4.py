from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcAsiaSessionFade(Strategy):
    METADATA = {
        "name": "BTC Asia Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Max hold until end of session (8 hours)
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 8    # UTC 08:00
        self.session_midpoint_hour = 4 # UTC 04:00
        self.lookback_period = 20    # For volatility and avg price
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.entry_cooldown_bars # Initialize to allow first trade

        # Store session data
        self.daily_open = None
        self.daily_high = None
        self.daily_low = None
        self.daily_volume = None
        self.session_mid_price = None
        self.session_max_deviation = 0.0

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
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset session data at the start of a new session (UTC 00:00)
        if current_hour == self.session_start_hour and (self.daily_open is None or ctx.bar.timestamp.day != self.daily_open["day"]):
            self.daily_open = {"price": ctx.bar.open, "day": current_day}
            self.daily_high = ctx.bar.high
            self.daily_low = ctx.bar.low
            self.daily_volume = ctx.bar.volume
            self.session_mid_price = None
            self.session_max_deviation = 0.0
        elif self.daily_open is None: # Wait until we have a daily open
             return None

        # Update session high/low/volume
        if current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
            self.daily_high = max(self.daily_high, ctx.bar.high)
            self.daily_low = min(self.daily_low, ctx.bar.low)
            self.daily_volume += ctx.bar.volume

        # Calculate session midpoint price (e.g., average of high/low or open/close during session)
        if current_hour >= self.session_start_hour and current_hour <= self.session_midpoint_hour:
            # Simple average of session high and low up to midpoint
            if self.daily_high is not None and self.daily_low is not None:
                self.session_mid_price = (self.daily_high + self.daily_low) / 2
        
        # Regime Filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # Avoid trading in crisis, strong trends, or extreme sentiment
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None
        if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None
        if fear_greed_index < 20 or fear_greed_index > 80: # Extreme fear/greed
            return None

        # Calculate volatility and average price movement to determine "stretched"
        volatility = self._stddev(closes, self.lookback_period)
        if volatility is None or volatility == 0:
            return None
        
        # Only consider trades during the second half of the Asia session (after midpoint)
        if current_hour > self.session_midpoint_hour and current_hour < self.session_end_hour:
            if self.session_mid_price is None or self.daily_open is None:
                return None

            # Calculate deviation from session midpoint
            deviation_from_mid = (ctx.bar.close - self.session_mid_price) / self.session_mid_price * 10000 # in bps

            # Define "stretched" based on historical volatility
            # A move is stretched if it exceeds 1.5x the average recent volatility
            stretch_threshold_bps = 1.5 * volatility / closes[-1] * 10000 # Convert stddev to bps

            # Check for cooldown
            if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
                return None

            if not ctx.has_position():
                # Long signal: price is significantly below midpoint and trying to revert
                if deviation_from_mid < -stretch_threshold_bps and ctx.bar.close > closes[-2]: # Price below midpoint, small bounce
                    # Only if it's not a strong bearish trend day (btc_return_pct)
                    if btc_return_pct > -0.5: # Not down too much on the day
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            horizon_seconds=(self.session_end_hour - current_hour) * 3600,
                            metadata={
                                "reason": "asia_session_fade_long",
                                "current_hour": current_hour,
                                "deviation_bps": round(deviation_from_mid, 2),
                                "stretch_threshold_bps": round(stretch_threshold_bps, 2),
                                "session_mid_price": round(self.session_mid_price, 2),
                                "market_regime": market_regime,
                                "fear_greed_index": fear_greed_index,
                            }
                        )

                # Short signal: price is significantly above midpoint and trying to revert
                elif deviation_from_mid > stretch_threshold_bps and ctx.bar.close < closes[-2]: # Price above midpoint, small dip
                    # Only if it's not a strong bullish trend day
                    if btc_return_pct < 0.5: # Not up too much on the day
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            horizon_seconds=(self.session_end_hour - current_hour) * 3600,
                            metadata={
                                "reason": "asia_session_fade_short",
                                "current_hour": current_hour,
                                "deviation_bps": round(deviation_from_mid, 2),
                                "stretch_threshold_bps": round(stretch_threshold_bps, 2),
                                "session_mid_price": round(self.session_mid_price, 2),
                                "market_regime": market_regime,
                                "fear_greed_index": fear_greed_index,
                            }
                        )

            # Exit logic: if we are close to the end of the session, or price reverts past midpoint
            if ctx.has_position():
                pos_direction = ctx.position_direction()
                entry_price = ctx.position["entry_price"]

                # Exit near session end
                if current_hour >= self.session_end_hour - 1: # Exit in the hour before session end
                    return ctx.signal(
                        "flat",
                        confidence=0.9,
                        metadata={
                            "reason": "session_end_exit",
                            "current_hour": current_hour,
                            "pos_direction": pos_direction,
                            "entry_price": round(entry_price, 2),
                            "current_price": round(ctx.bar.close, 2),
                        }
                    )
                
                # Exit if price reverts strongly past the midpoint
                if pos_direction == "long" and ctx.bar.close > self.session_mid_price * (1 + 0.0005): # 5 bps past midpoint
                     return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "long_revert_past_midpoint_exit",
                            "current_hour": current_hour,
                            "entry_price": round(entry_price, 2),
                            "current_price": round(ctx.bar.close, 2),
                            "session_mid_price": round(self.session_mid_price, 2),
                        }
                    )
                elif pos_direction == "short" and ctx.bar.close < self.session_mid_price * (1 - 0.0005): # 5 bps past midpoint
                     return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "short_revert_past_midpoint_exit",
                            "current_hour": current_hour,
                            "entry_price": round(entry_price, 2),
                            "current_price": round(ctx.bar.close, 2),
                            "session_mid_price": round(self.session_mid_price, 2),
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to current bar index on close to enforce cooldown
        self.last_trade_bar = ctx.bar_index