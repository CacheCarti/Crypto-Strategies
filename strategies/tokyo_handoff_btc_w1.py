from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TokyoHandoffContinuation(Strategy):
    METADATA = {
        "name": "TokyoHandoffContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 8, # Up to 8 hours
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.tokyo_open_hour = 0  # UTC 00:00
        self.tokyo_close_hour = 8  # UTC 08:00
        self.europe_handoff_hour = 9 # UTC 09:00 - when Europe session is active
        self.us_handoff_hour = 13 # UTC 13:00 - when US session is active
        self.exit_before_next_tokyo = 23 # Exit before UTC 23:00

        self.min_tokyo_move_pct = 0.5 # Minimum move percentage in Tokyo session to consider
        self.max_tokyo_move_pct = 3.0 # Maximum move percentage to avoid exhausted moves

        self.rsi_period = 14
        self.ema_fast_period = 10
        self.ema_slow_period = 30

        self.last_signal_bar = -self.METADATA["warmup_bars"] # Cooldown logic
        self.cooldown_bars = 4 # Wait 4 bars (4 hours) after a trade

        self.daily_high = -math.inf
        self.daily_low = math.inf
        self.tokyo_open_price = None
        self.tokyo_move_direction = None
        self.tokyo_move_magnitude = 0.0
        self.current_day = None

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
            return 100.0 if avg_gain > 0 else 50.0 # Prevent division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first EMA
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily state at the start of a new day (Tokyo open)
        if current_day != self.current_day:
            self.current_day = current_day
            self.tokyo_open_price = None
            self.tokyo_move_direction = None
            self.tokyo_move_magnitude = 0.0
            self.daily_high = -math.inf
            self.daily_low = math.inf
            # print(f"--- New Day: {ctx.bar.timestamp.date()} ---")

        # Track daily high/low for Tokyo session
        self.daily_high = max(self.daily_high, ctx.bar.high)
        self.daily_low = min(self.daily_low, ctx.bar.low)

        # Tokyo session logic (UTC 00:00 to 08:00)
        if current_hour == self.tokyo_open_hour:
            if self.tokyo_open_price is None: # Only set once per day
                self.tokyo_open_price = ctx.bar.open
                # print(f"Tokyo Open Price set: {self.tokyo_open_price}")
        
        if self.tokyo_open_price is not None and current_hour <= self.tokyo_close_hour:
            # Calculate move magnitude
            current_tokyo_price = ctx.bar.close
            move_pct = ((current_tokyo_price - self.tokyo_open_price) / self.tokyo_open_price) * 100.0
            self.tokyo_move_magnitude = abs(move_pct)
            self.tokyo_move_direction = "long" if move_pct > 0 else "short" if move_pct < 0 else None
            # print(f"Tokyo session: current_hour={current_hour}, move_pct={move_pct:.2f}%, magnitude={self.tokyo_move_magnitude:.2f}%")

        # Exit logic for existing position
        if ctx.has_position():
            # Exit before next Tokyo session (UTC 23:00)
            if current_hour >= self.exit_before_next_tokyo:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "exit_before_next_tokyo_session",
                    "current_hour": current_hour,
                    "tokyo_open_price": self.tokyo_open_price,
                    "tokyo_move_magnitude": self.tokyo_move_magnitude
                })
            return None # Hold position

        # Cooldown period
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Entry logic (Europe/US handoff)
        if self.tokyo_open_price is not None and \
           self.europe_handoff_hour <= current_hour <= self.us_handoff_hour + 2: # Trade during Europe/US overlap
            
            # Check Tokyo move conditions
            if not (self.min_tokyo_move_pct <= self.tokyo_move_magnitude <= self.max_tokyo_move_pct):
                # print(f"Skipping entry: Tokyo move magnitude {self.tokyo_move_magnitude:.2f}% not within [{self.min_tokyo_move_pct}, {self.max_tokyo_move_pct}]%")
                return None
            
            # Check for exhaustion (RSI)
            rsi = self._rsi(closes, self.rsi_period)
            if rsi is None:
                return None

            # Check for trend (EMA crossover, not just current price vs EMA)
            ema_fast_prev = self._ema(closes[:-1], self.ema_fast_period)
            ema_slow_prev = self._ema(closes[:-1], self.ema_slow_period)
            ema_fast_curr = self._ema(closes, self.ema_fast_period)
            ema_slow_curr = self._ema(closes, self.ema_slow_period)

            if ema_fast_curr is None or ema_slow_curr is None or ema_fast_prev is None or ema_slow_prev is None:
                return None

            # Additional filters
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

            signal_direction = None
            reason = ""
            confidence = 0.0

            # Long condition: Tokyo bullish, continuation in Europe/US, not overbought, EMA uptrend
            if self.tokyo_move_direction == "long":
                if ctx.bar.close > ema_fast_curr and ctx.bar.close > ema_slow_curr: # Price above EMAs
                    if ema_fast_prev <= ema_slow_prev and ema_fast_curr > ema_slow_curr: # EMA crossover confirmation
                        if rsi < 70: # Not overbought
                            # Additional confidence from fear/greed and BTC momentum
                            if fear_greed_index > 40 and btc_return_pct > 0:
                                signal_direction = "long"
                                reason = "tokyo_bullish_continuation_ema_cross_rsi_not_overbought"
                                confidence = 0.7 + (fear_greed_index / 100 * 0.1) + (min(btc_return_pct, 1.0) * 0.1)
                                confidence = min(confidence, 1.0) # Cap confidence

            # Short condition: Tokyo bearish, continuation in Europe/US, not oversold, EMA downtrend
            elif self.tokyo_move_direction == "short":
                if ctx.bar.close < ema_fast_curr and ctx.bar.close < ema_slow_curr: # Price below EMAs
                    if ema_fast_prev >= ema_slow_prev and ema_fast_curr < ema_slow_curr: # EMA crossover confirmation
                        if rsi > 30: # Not oversold
                            # Additional confidence from fear/greed and BTC momentum
                            if fear_greed_index < 60 and btc_return_pct < 0:
                                signal_direction = "short"
                                reason = "tokyo_bearish_continuation_ema_cross_rsi_not_oversold"
                                confidence = 0.7 + ((100 - fear_greed_index) / 100 * 0.1) + (max(btc_return_pct, -1.0) * -0.1)
                                confidence = min(confidence, 1.0) # Cap confidence

            if signal_direction:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": reason,
                        "tokyo_open_price": self.tokyo_open_price,
                        "tokyo_move_magnitude": f"{self.tokyo_move_magnitude:.2f}%",
                        "tokyo_move_direction": self.tokyo_move_direction,
                        "current_price": ctx.bar.close,
                        "rsi": f"{rsi:.2f}",
                        "ema_fast": f"{ema_fast_curr:.2f}",
                        "ema_slow": f"{ema_slow_curr:.2f}",
                        "fear_greed_index": fear_greed_index,
                        "btc_return_pct": f"{btc_return_pct:.2f}%"
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # For this strategy, no adaptive logic needed on close, just logging.
        # Cooldown is handled in on_bar.
        pass