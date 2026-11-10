from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapFade(Strategy):
    METADATA = {
        "name": "US Session Gap Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.gap_threshold_pct = 0.5 # 0.5% gap
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars
        self.ema_period = 20 # For trend filter
        self.rsi_period = 14 # For confirmation

        # To store US session close and next Asia open
        self.us_close_price = None
        self.us_close_bar_index = -1
        self.us_close_timestamp = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
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
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close
        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_weekday = current_time.weekday() # Monday is 0, Sunday is 6

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        # Determine US session close (e.g., 21:00 UTC)
        # Store the US session close price for gap calculation
        if current_hour == 21: # Example: US session closes at 21:00 UTC
            self.us_close_price = current_price
            self.us_close_bar_index = ctx.bar_index
            self.us_close_timestamp = current_time
            return None # Don't trade on the closing bar itself

        # Check for Asia session open after a US close
        # Asia session typically opens around 00:00 - 02:00 UTC
        # We need to ensure a US close was recorded before checking for a gap
        if self.us_close_price is not None and \
           ctx.bar_index > self.us_close_bar_index and \
           (current_hour == 0 or current_hour == 1) and \
           not ctx.has_position():

            # Calculate gap
            gap_pct = ((current_price - self.us_close_price) / self.us_close_price) * 100

            # Get indicators for confirmation
            ema = self._ema(closes, self.ema_period)
            rsi = self._rsi(closes, self.rsi_period)
            if ema is None or rsi is None:
                return None

            # Regime filters
            fear_greed = ctx.features.get("fear_greed_index", 50)
            eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
            
            signal_direction = None
            confidence = 0.0
            reason = ""

            # LONG signal: Significant gap down
            if gap_pct < -self.gap_threshold_pct:
                # Confirm with oversold RSI and price below EMA (indicating a potential bounce)
                if rsi < 40 and current_price < ema:
                    signal_direction = "long"
                    confidence = 0.6 + (0.4 * ctx.rng.uniform(0,1)) # Add some randomness for diversity
                    reason = "asia_open_gap_down_fade_rsi_oversold"
                    metadata = {
                        "gap_pct": round(gap_pct, 2),
                        "us_close": round(self.us_close_price, 2),
                        "asia_open": round(current_price, 2),
                        "rsi": round(rsi, 2),
                        "ema": round(ema, 2),
                        "fear_greed": fear_greed,
                        "eth_return_pct": round(eth_return_pct, 2),
                    }
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(signal_direction, confidence=confidence, metadata=metadata)

            # SHORT signal: Significant gap up
            elif gap_pct > self.gap_threshold_pct:
                # Confirm with overbought RSI and price above EMA (indicating a potential fade)
                if rsi > 60 and current_price > ema:
                    signal_direction = "short"
                    confidence = 0.6 + (0.4 * ctx.rng.uniform(0,1))
                    reason = "asia_open_gap_up_fade_rsi_overbought"
                    metadata = {
                        "gap_pct": round(gap_pct, 2),
                        "us_close": round(self.us_close_price, 2),
                        "asia_open": round(current_price, 2),
                        "rsi": round(rsi, 2),
                        "ema": round(ema, 2),
                        "fear_greed": fear_greed,
                        "eth_return_pct": round(eth_return_pct, 2),
                    }
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(signal_direction, confidence=confidence, metadata=metadata)

        # Position management
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]

            # Exit if gap is filled or nearing next US session close
            # Gap fill logic: price crosses the US close price
            if direction == "long" and current_price >= self.us_close_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "gap_filled_long",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(current_price, 2),
                    "us_close": round(self.us_close_price, 2),
                })
            elif direction == "short" and current_price <= self.us_close_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "gap_filled_short",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(current_price, 2),
                    "us_close": round(self.us_close_price, 2),
                })

            # Exit before next US session (e.g., 20:00 UTC) to avoid holding over another close
            if current_hour == 20 and self.us_close_timestamp is not None and \
               (current_time - self.us_close_timestamp).total_seconds() > 3600 * 18: # If we're past 18 hours since the US close
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_before_next_us_session",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(current_price, 2),
                    "current_hour": current_hour,
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset US close price after a trade is closed to ensure a fresh gap calculation
        # This prevents using a stale US close price if a trade was held for multiple sessions
        self.us_close_price = None
        self.us_close_bar_index = -1
        self.us_close_timestamp = None
        # self.last_trade_bar_index = ctx.bar_index # Cooldown is handled in on_bar