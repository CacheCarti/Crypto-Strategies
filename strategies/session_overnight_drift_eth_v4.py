from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightDrift(Strategy):
    METADATA = {
        "name": "EthOvernightDrift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Average 4-hour hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00
        self.us_session_start_hour = 13 # UTC 13:00 (approximate US open)
        self.min_session_move_factor = 1.5 # How many times median range the session move needs to be
        self.median_range_period = 20 # Period for median range calculation
        self.min_move_pct = 0.003 # Minimum 0.3% move
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.last_signal_hour = -1 # To ensure only one signal per session
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.volatility_threshold_high = 0.005 # 0.5% ATR for high volatility
        self.volatility_threshold_low = 0.001 # 0.1% ATR for low volatility

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs: return None # Handle case where trs is empty if period is too small
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Cooldown period check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate regime filters
        current_atr = self._atr(highs, lows, closes, period=14)
        if current_atr is None:
            return None
        
        # Absolute ATR value as percentage of price
        atr_pct = (current_atr / current_close) if current_close else 0.0

        fast_ema = self._ema(closes, self.ema_fast_period)
        slow_ema = self._ema(closes, self.ema_slow_period)

        if fast_ema is None or slow_ema is None:
            return None
        
        # Trend regime based on EMA crossover
        trend_regime = "neutral"
        if fast_ema > slow_ema and closes[-1] > slow_ema:
            trend_regime = "bullish"
        elif fast_ema < slow_ema and closes[-1] < slow_ema:
            trend_regime = "bearish"

        # Volatility regime
        volatility_regime = "normal"
        if atr_pct > self.volatility_threshold_high:
            volatility_regime = "high"
        elif atr_pct < self.volatility_threshold_low:
            volatility_regime = "low"

        # Check for overnight session close
        if current_hour == self.overnight_end_hour:
            # Only trigger once per day, reset `last_signal_hour` at a different time
            if self.last_signal_hour == self.overnight_end_hour:
                return None
            
            # Find the open of the overnight session
            # We need to look back to self.overnight_start_hour on the previous day
            session_open_price = None
            session_low = float('inf')
            session_high = float('-inf')
            session_closes = []

            # Find the bars within the overnight session
            # This requires iterating backwards until the start of the overnight session (or end of previous day)
            # Given the 1-hour bar, we need to go back up to 6 bars.
            # We assume ctx.closes() provides enough history.
            
            # Find the actual bar that started the overnight session *today*
            # Or the bar that was the first bar of the overnight session.
            # This is tricky with fixed length arrays. Let's simplify.
            # Assume we are at 06:00 UTC. The session started at 00:00 UTC.
            # We need the open of the bar at 00:00 UTC, and the high/low/close of all bars from 00:00 to 06:00.
            
            # For simplicity, let's look back 'self.overnight_end_hour - self.overnight_start_hour' bars
            # which is 6 bars.
            if len(closes) < self.overnight_end_hour - self.overnight_start_hour + 1:
                return None

            # The 00:00 UTC bar would be closes[-7] (if current is closes[-1] at 06:00)
            # The open for that bar is ctx.opens()[-7]
            # This is brittle. Better to find the actual bar by timestamp.
            
            overnight_bars = []
            for i in range(len(closes) - 1, -1, -1):
                bar_timestamp = ctx.bar.timestamp - (ctx.bar_index - (len(closes) - 1 - i)) * (3600) # Roughly
                # This timestamp calculation is not accurate across DST or various bar intervals.
                # A better approach would be to store the open price of the 00:00 bar when it occurs.
                # Since we don't have that, we approximate by taking the open of the bar `overnight_end_hour - overnight_start_hour` bars ago.
                
                # Let's assume we are at the 06:00 bar. The 00:00 bar is 6 bars ago.
                # We need the open of the bar at 00:00, and the high/low of bars from 00:00 to 06:00.
                
                # Find the open of the bar at 00:00 UTC (6 bars before current bar)
            if len(ctx.opens(7)) < 7: return None # Need at least 7 bars (current + 6 previous)
            session_open_price = ctx.opens(7)[0] # Open of the bar 6 hours ago (00:00 UTC)
            
            # Calculate session high/low/close from 00:00 UTC to 06:00 UTC
            session_high = max(highs[-7:]) # From 00:00 to 06:00
            session_low = min(lows[-7:])   # From 00:00 to 06:00
            
            if session_open_price is None:
                return None

            session_move = current_close - session_open_price
            session_range = session_high - session_low
            
            # Calculate median range for the past median_range_period
            ranges = [h - l for h, l in zip(highs[-self.median_range_period:], lows[-self.median_range_period:])]
            if not ranges: return None
            ranges.sort()
            median_range = ranges[len(ranges) // 2]
            if median_range == 0: return None

            # Check if the move is decisive and not a single-bar spike
            is_decisive_move = abs(session_move) > (median_range * self.min_session_move_factor) and \
                                abs(session_move / current_close) > self.min_move_pct
            
            # Ensure it's not a single-bar spike (i.e., range is not dominated by one bar)
            # This is hard to check robustly with only bar history.
            # A simple proxy: ensure the session_range is also significant.
            is_not_spike = session_range > abs(session_move) * 0.5 # Session range at least half of the move.

            if is_decisive_move and is_not_spike:
                signal_direction = None
                confidence = 0.0

                # Regime filtering
                if volatility_regime == "high": # Avoid trading in extremely high volatility
                    return None
                if volatility_regime == "low": # Avoid trading in extremely low volatility
                    return None

                if session_move > 0: # Bullish overnight move
                    if trend_regime == "bullish": # Only follow with trend
                        signal_direction = "long"
                        confidence = 0.7
                elif session_move < 0: # Bearish overnight move
                    if trend_regime == "bearish": # Only follow with trend
                        signal_direction = "short"
                        confidence = 0.7

                if signal_direction:
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_hour = current_hour # Mark that we've signaled for this session

                    # Exit before next US session if position is still open
                    horizon_seconds = (self.us_session_start_hour - current_hour) * 3600
                    if horizon_seconds <= 0: # If US session already started or passed, exit by end of day
                        horizon_seconds = 3600 * 12 # Roughly end of day if we miss US session start

                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        stop_loss_bps=self.declared_sl_bps,
                        take_profit_bps=self.declared_tp_bps,
                        horizon_seconds=horizon_seconds,
                        metadata={
                            "reason": f"overnight_drift_follow_{signal_direction}",
                            "session_open": session_open_price,
                            "session_close": current_close,
                            "session_move_pct": (session_move / session_open_price) * 100,
                            "median_range": median_range,
                            "atr_pct": atr_pct * 100,
                            "trend_regime": trend_regime,
                            "volatility_regime": volatility_regime,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "fear_greed_index": ctx.features.get("fear_greed_index", 50),
                        }
                    )
        
        # Reset last_signal_hour after the US session starts, so we can signal again next day
        if current_hour == self.us_session_start_hour and self.last_signal_hour != -1:
            self.last_signal_hour = -1

        # If we have a position and it's approaching the US session, consider exiting
        if ctx.has_position() and ctx.position_direction() != "flat":
            if current_hour >= self.us_session_start_hour - 1: # One hour before US session
                # If we entered based on overnight drift, it's time to exit before US session
                if ctx.position["metadata"].get("reason", "").startswith("overnight_drift_follow"):
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "exit_before_us_session",
                            "current_price": current_close,
                            "entry_price": ctx.position["entry_price"],
                            "bars_held": ctx.bar_index - self.last_trade_bar, # Approximate
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We don't need to do anything specific here for this strategy,
        # but it's available for logging or adaptive sizing.
        pass