from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightFade(Strategy):
    METADATA = {
        "name": "Overnight Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0
        self.session_end_hour = 6
        self.exit_before_hour = 12 # Exit before US session opens fully
        self.max_session_bars = self.session_end_hour - self.session_start_hour
        self.min_session_move_pct = 0.5 # Minimum move to consider fading
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # Thresholds for fading
        self.rsi_period = 14
        self.rsi_overbought = 70
        self.rsi_oversold = 30
        self.zscore_period = 20
        self.zscore_threshold = 1.5 # How many std devs away from session mean

        # Track session data
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_open = -1.0
        self.session_close_price = -1.0
        self.session_bars_count = 0
        self.session_active = False

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

    def _zscore(self, values, period):
        if len(values) < period:
            return 0.0 # Return 0 if not enough data
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if mean is None or std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Session management
        if current_hour == self.session_start_hour and not self.session_active:
            # New session starts
            self.session_active = True
            self.session_open = current_close
            self.session_high = current_close
            self.session_low = current_close
            self.session_bars_count = 0
        elif current_hour >= self.session_start_hour and current_hour < self.session_end_hour and self.session_active:
            # During session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_close_price = current_close
            self.session_bars_count += 1
        elif current_hour == self.session_end_hour and self.session_active:
            # Session ends, reset for next day
            self.session_active = False # Session is over, but we can still trade on the last bar
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_close_price = current_close
            self.session_bars_count += 1
        elif current_hour >= self.session_end_hour and self.session_active:
            # If session active but hour passed, it means we missed the end_hour transition
            # This can happen if strategy starts mid-session. Reset for safety.
            self.session_active = False

        # Exit existing position before next US session
        if ctx.has_position() and current_hour == self.exit_before_hour:
            return ctx.signal("flat", confidence=0.7, metadata={
                "reason": "session_end_exit",
                "current_hour": current_hour,
                "exit_before_hour": self.exit_before_hour
            })

        # Do not trade if cooldown is active
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Do not trade in crisis regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Do not fade strong trends (simple check: recent BTC return too strong)
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        if abs(btc_return) > 1.5: # If BTC moved more than 1.5% in 24h, assume strong trend
            return None

        # Entry logic: Fade stretched overnight session
        if not ctx.has_position() and self.session_active and self.session_bars_count >= self.max_session_bars - 2: # Near end of session
            session_range = self.session_high - self.session_low
            session_midpoint = (self.session_high + self.session_low) / 2.0
            
            if session_range == 0:
                return None

            session_move_pct = abs(current_close - self.session_open) / self.session_open * 100.0

            # Check if session move is significant
            if session_move_pct > self.min_session_move_pct:
                # Calculate RSI and Z-score for confirmation
                current_rsi = self._rsi(closes, self.rsi_period)
                current_zscore = self._zscore(closes, self.zscore_period)

                if current_rsi is None:
                    return None

                # Long signal: Stretched down, near session low, oversold
                if current_close < session_midpoint and \
                   (current_close - self.session_low) / session_range < 0.2 and \
                   current_rsi < self.rsi_oversold and \
                   current_zscore < -self.zscore_threshold:
                    
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "overnight_fade_long",
                        "session_move_pct": round(session_move_pct, 2),
                        "session_range": round(session_range, 2),
                        "session_midpoint": round(session_midpoint, 2),
                        "current_price": round(current_close, 2),
                        "rsi": round(current_rsi, 2),
                        "zscore": round(current_zscore, 2),
                    })

                # Short signal: Stretched up, near session high, overbought
                elif current_close > session_midpoint and \
                     (self.session_high - current_close) / session_range < 0.2 and \
                     current_rsi > self.rsi_overbought and \
                     current_zscore > self.zscore_threshold:
                    
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "overnight_fade_short",
                        "session_move_pct": round(session_move_pct, 2),
                        "session_range": round(session_range, 2),
                        "session_midpoint": round(session_midpoint, 2),
                        "current_price": round(current_close, 2),
                        "rsi": round(current_rsi, 2),
                        "zscore": round(current_zscore, 2),
                    })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could use this to adapt self.cooldown_bars or confidence, but keeping it simple for now.
        pass