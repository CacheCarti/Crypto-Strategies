from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCSessionDrift(Strategy):
    METADATA = {
        "name": "BTC Session Drift",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6 hours max hold
        "warmup_bars": 50, # Need enough bars for ATR and median range
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20  # For ATR and median range
        self.entry_cooldown_bars = 6 # Cooldown after a trade
        self.last_trade_bar = -self.entry_cooldown_bars
        self.last_signal_timestamp = None
        self.session_start_hour = 0 # UTC 00:00 start of overnight session
        self.session_end_hour = 6   # UTC 06:00 end of overnight session
        self.target_exit_hour = 12  # Exit before US session (UTC 12:00)

        # Thresholds
        self.move_threshold_multiplier = 1.0 # Overnight move must exceed 1.0 * median_range
        self.min_confidence = 0.6
        self.max_confidence = 0.8
        self.min_volatility_multiplier = 0.8 # Only trade if current ATR is not too low
        self.max_volatility_multiplier = 2.0 # And not too high

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
        volumes = ctx.volumes(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for position management first
        if ctx.has_position():
            # Exit before target_exit_hour if still holding
            if current_hour == self.target_exit_hour and ctx.bar_index > self.last_trade_bar + self.entry_cooldown_bars:
                # Only close if the signal wasn't generated in the current bar
                if self.last_signal_timestamp != ctx.bar.timestamp:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position otherwise

        # Cooldown period after a previous trade
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # Calculate indicators
        current_atr = self._atr(highs, lows, closes, self.lookback_period)
        median_range = self._median_range(highs, lows, self.lookback_period)

        if current_atr is None or median_range is None:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Only trade during normal or high vol, avoid crisis
        if regime == "CRISIS" or regime == "MELTDOWN":
            return None
        
        # Only trade if volatility is within reasonable bounds
        if current_atr < median_range * self.min_volatility_multiplier or \
           current_atr > median_range * self.max_volatility_multiplier:
            return None

        # Entry logic: look for decisive move in the overnight session
        # We process at the end of the session (hour == self.session_end_hour)
        if current_hour == self.session_end_hour:
            # Calculate the price change during the overnight session (00:00 to 06:00 UTC)
            # We need the close at 00:00 (i.e. close of 23:00 bar) and close at 06:00 (current bar)
            # Assuming ctx.closes() is ordered oldest first
            
            # Find the index of the bar that closed at 00:00 UTC (start of session)
            session_start_bar_idx = -1
            for i in range(len(ctx.closes(self.lookback_period * 2)) - 1, -1, -1):
                past_ts = ctx.bar.timestamp - (ctx.bar_index - (len(ctx.closes(self.lookback_period * 2)) - 1 - i)) * ctx.bar_duration_seconds
                if past_ts.hour == self.session_start_hour and past_ts.minute == 0:
                    session_start_bar_idx = i
                    break
            
            if session_start_bar_idx == -1 or session_start_bar_idx >= len(closes) - 1:
                return None # Not enough history for the session

            overnight_start_price = closes[session_start_bar_idx]
            overnight_end_price = closes[-1]
            overnight_move = overnight_end_price - overnight_start_price
            overnight_move_pct = (overnight_move / overnight_start_price) * 100

            # Check if the move is decisive
            absolute_move = abs(overnight_move)
            required_move = median_range * self.move_threshold_multiplier

            # Skip if the move was just a single bar spike (i.e. current bar is too large compared to total move)
            if abs(closes[-1] - closes[-2]) > 0.7 * absolute_move: # If current bar accounts for >70% of the move
                return None

            # Determine signal direction and confidence
            signal_direction = None
            confidence = 0.0
            reason = ""

            if overnight_move > required_move: # Bullish drift
                signal_direction = "long"
                confidence = self.min_confidence + (overnight_move_pct / 0.5) * (self.max_confidence - self.min_confidence) # Scale confidence by move size
                confidence = min(confidence, self.max_confidence)
                reason = "overnight_bullish_drift_after_session"
                
                # Further filter by trend and sentiment
                if trend_regime == "bear" or fear_greed < 40:
                    return None # Don't go long in a bearish trend or high fear

            elif overnight_move < -required_move: # Bearish drift
                signal_direction = "short"
                confidence = self.min_confidence + (abs(overnight_move_pct) / 0.5) * (self.max_confidence - self.min_confidence)
                confidence = min(confidence, self.max_confidence)
                reason = "overnight_bearish_drift_after_session"

                # Further filter by trend and sentiment
                if trend_regime == "bull" or fear_greed > 60:
                    return None # Don't go short in a bullish trend or high greed

            if signal_direction and confidence > 0.5:
                self.last_trade_bar = ctx.bar_index
                self.last_signal_timestamp = ctx.bar.timestamp
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "session_start_price": overnight_start_price,
                        "session_end_price": overnight_end_price,
                        "overnight_move_pct": overnight_move_pct,
                        "required_move_usd": required_move,
                        "current_atr": current_atr,
                        "median_range": median_range,
                        "fear_greed": fear_greed,
                        "trend_regime": trend_regime,
                        "current_hour": current_hour,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, reset cooldown or adapt logic based on trade outcome
        # For this strategy, the cooldown is handled in on_bar, but could be reset here
        pass