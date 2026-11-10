from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionFade(Strategy):
    METADATA = {
        "name": "EuropeanSessionFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended hold time up to 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC 7
        self.session_end_hour = 12   # UTC 12
        self.fade_trigger_hour = 10  # Start looking for fades around UTC 10
        self.fade_window_bars = 3    # Look back 3 bars for session extreme
        self.strength_threshold = 0.005 # 0.5% move in session to consider "stretched"
        self.last_trade_bar_index = -1
        self.cooldown_bars = 6       # Cooldown for 6 bars (6 hours) after a trade
        self.ema_period = 20         # For trend filter
        self.stddev_period = 20      # For volatility assessment

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Avoid trading in crisis or strong trending regimes
        if regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"]:
            return None

        # European session logic
        if current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
            # Only consider fades after a certain point in the session
            if current_hour < self.fade_trigger_hour:
                return None

            # Calculate session range and initial price
            session_closes = []
            session_highs = []
            session_lows = []
            session_volumes = []
            session_start_price = None

            # Collect data for the current session up to the current bar
            # This requires iterating backwards or storing session data.
            # For simplicity, we'll assume `closes` provides enough history
            # and find the earliest bar within the session.
            
            # Find the start of the current session within the available bars
            session_bars = []
            for i in range(len(closes) - 1, -1, -1):
                bar_timestamp = ctx.bar.timestamp - (ctx.bar_index - (len(closes) - 1 - i)) * ctx.bar_duration
                if bar_timestamp.hour >= self.session_start_hour and bar_timestamp.hour < self.session_end_hour:
                    session_bars.append((closes[i], highs[i], lows[i]))
                else:
                    # Break if we've gone before the session start or too far back
                    if bar_timestamp.hour < self.session_start_hour and bar_timestamp.hour < current_hour:
                        break
            session_bars.reverse() # Oldest first

            if not session_bars:
                return None

            session_open_price = session_bars[0][0]
            session_high = max(b[1] for b in session_bars)
            session_low = min(b[2] for b in session_bars)

            # Calculate the current session's price range relative to its open
            move_up = (current_price - session_open_price) / session_open_price
            move_down = (session_open_price - current_price) / session_open_price

            # Fade logic: if price is stretched and stalls near session high/low
            # Look for signs of stalling: current price is near session high/low,
            # but recent bars within the fade window show less momentum or reversal.

            ema_val = self._ema(closes, self.ema_period)
            if ema_val is None:
                return None

            # Check for volatility using stddev
            stddev_val = self._stddev(closes, self.stddev_period)
            if stddev_val is None or stddev_val == 0:
                return None
            
            # Use Bollinger Band concept for "stretched"
            upper_band = ema_val + 2 * stddev_val
            lower_band = ema_val - 2 * stddev_val

            signal_direction = None
            reason = ""
            confidence = 0.0

            # Long signal: price is stretched down, near session low/lower band, and potentially reversing
            if move_down > self.strength_threshold and current_price < lower_band:
                # Check for a slight bounce or stall after a strong move down
                # For simplicity, let's check if the current close is higher than the previous bar's close
                # and if it's near the session low but not making new lows rapidly.
                if current_price > closes[-2]: # Simple reversal sign
                    signal_direction = "long"
                    reason = "session_fade_long_oversold_bounce"
                    confidence = min(0.8, move_down * 10) # Scale confidence by how stretched it is
            
            # Short signal: price is stretched up, near session high/upper band, and potentially reversing
            elif move_up > self.strength_threshold and current_price > upper_band:
                # Check for a slight dip or stall after a strong move up
                if current_price < closes[-2]: # Simple reversal sign
                    signal_direction = "short"
                    reason = "session_fade_short_overbought_dip"
                    confidence = min(0.8, move_up * 10) # Scale confidence by how stretched it is

            if signal_direction and not ctx.has_position():
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": reason,
                        "current_price": current_price,
                        "session_open": session_open_price,
                        "session_high": session_high,
                        "session_low": session_low,
                        "move_up_pct": move_up * 100,
                        "move_down_pct": move_down * 100,
                        "ema_val": ema_val,
                        "stddev_val": stddev_val,
                        "upper_band": upper_band,
                        "lower_band": lower_band,
                        "fear_greed_index": fear_greed,
                        "funding_rate": funding_rate,
                    }
                )

        # Exit logic for positions held past the session end, or if conditions reverse
        if ctx.has_position():
            # Exit near the end of the session, or if it's held too long
            if current_hour >= self.session_end_hour and ctx.position_direction() is not None:
                self.last_trade_bar_index = ctx.bar_index # Reset cooldown
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "session_end_exit",
                        "current_price": current_price,
                        "entry_price": ctx.position["entry_price"],
                        "bars_held": ctx.bar_index - ctx.position["entry_bar_index"],
                        "exit_hour": current_hour,
                    }
                )
            
            # Alternatively, if price retraces significantly towards the session open, consider exiting.
            # This is a simple discretionary exit not tied to SL/TP.
            session_open_price = self.get_session_open_price(ctx) # Helper to get session open
            if session_open_price:
                if ctx.position_direction() == "long" and current_price < session_open_price:
                    if (session_open_price - current_price) / session_open_price > self.strength_threshold / 2: # Retraced halfway to open
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "flat",
                            confidence=0.3,
                            metadata={
                                "reason": "fade_reversal_to_session_open",
                                "current_price": current_price,
                                "entry_price": ctx.position["entry_price"],
                                "session_open": session_open_price,
                            }
                        )
                elif ctx.position_direction() == "short" and current_price > session_open_price:
                    if (current_price - session_open_price) / session_open_price > self.strength_threshold / 2: # Retraced halfway to open
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "flat",
                            confidence=0.3,
                            metadata={
                                "reason": "fade_reversal_to_session_open",
                                "current_price": current_price,
                                "entry_price": ctx.position["entry_price"],
                                "session_open": session_open_price,
                            }
                        )
        return None

    def get_session_open_price(self, ctx: BarContext) -> Optional[float]:
        # This is a simplified way to get the session open.
        # A more robust way would be to store this when the session starts.
        # For this strategy, we look back until we find a bar outside the current session.
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < 2:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        for i in range(len(closes) - 1, -1, -1):
            bar_timestamp = ctx.bar.timestamp - (ctx.bar_index - (len(closes) - 1 - i)) * ctx.bar_duration
            if bar_timestamp.hour < self.session_start_hour or bar_timestamp.hour >= self.session_end_hour:
                # The next bar (i+1) would be the start of the session
                if i + 1 < len(closes):
                    return closes[i+1] # This is technically the close of the previous bar, which is the open of current bar if we only had opens.
                                       # For simplicity, using close[i+1] as an approximation for the session start price.
                break
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log or adjust strategy parameters based on trade outcome
        pass