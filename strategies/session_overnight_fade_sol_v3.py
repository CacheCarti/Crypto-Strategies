from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionFadeSOL(Strategy):
    METADATA = {
        "name": "OvernightSessionFadeSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 0  # UTC 00:00
        self.entry_hour_end = 6    # UTC 06:00
        self.exit_hour = 12        # UTC 12:00, before US session starts fully
        self.cooldown_bars = 5
        self.last_signal_bar_index = -self.cooldown_bars

        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.session_lookback_period = 6 # 6 hours for overnight session high/low

        # Store session start price to calculate session move
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Reset session data at the start of a new session (UTC 00:00)
        if current_hour == self.entry_hour_start and ctx.bar_index != self.session_start_bar_index:
            self.session_open_price = current_close
            self.session_high = current_close
            self.session_low = current_close
            self.session_start_bar_index = ctx.bar_index
        elif self.session_open_price is None and current_hour > self.entry_hour_start:
            # If strategy started mid-session, try to initialize session_open_price
            # This is a rough approximation and might not be precise for the very first session
            if ctx.bar_index - self.session_start_bar_index > self.session_lookback_period:
                self.session_open_price = closes[-(current_hour - self.entry_hour_start) -1] if (current_hour - self.entry_hour_start) < len(closes) else None
                self.session_high = max(highs[-(current_hour - self.entry_hour_start):]) if (current_hour - self.entry_hour_start) > 0 else current_close
                self.session_low = min(lows[-(current_hour - self.entry_hour_start):]) if (current_hour - self.entry_hour_start) > 0 else current_close
            else:
                self.session_open_price = current_close # Default if not enough history
                self.session_high = current_close
                self.session_low = current_close
        
        if self.session_open_price is not None:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit existing position before the next US session or if conditions reverse
        if ctx.has_position():
            if current_hour == self.exit_hour and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "exit_hour": self.exit_hour})

        # Cooldown period
        if ctx.bar_index < self.last_signal_bar_index + self.cooldown_bars:
            return None

        # Only consider entries within the overnight session window
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None # Avoid trading in crisis regimes

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None # Do not fade strong confirmed trends

        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index > 70 or fear_greed_index < 30:
            return None # Avoid extreme sentiment periods

        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        # Avoid fading against strong positive funding (longs paying shorts)
        if funding_rate > 0.0005: # e.g. 0.05% per 8h funding
            return None
        # Avoid fading against strong negative funding (shorts paying longs)
        if funding_rate < -0.0005:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None or self.session_open_price is None:
            return None

        # Strategy logic: Fade stretched session moves
        session_move_pct = ((current_close - self.session_open_price) / self.session_open_price) * 100 if self.session_open_price else 0.0
        
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry: price is significantly below session open, indicating a stretched move down
        # and current price is near session low, but bouncing or stalling.
        # RSI is oversold and fast EMA crosses above slow EMA or is about to.
        if (session_move_pct < -0.5 and # Stretched down move
            current_close < fast_ema and # Currently below fast EMA (shows recent weakness)
            rsi < 35 and # Oversold
            fast_ema > slow_ema * 0.995 and # Fast EMA close to or above slow EMA (potential reversal)
            current_close > self.session_low * 1.001 # Not making new session lows aggressively
            ):
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = 0.75
                reason = "session_overnight_fade_long_stretched_down"
        
        # Short entry: price is significantly above session open, indicating a stretched move up
        # and current price is near session high, but stalling.
        # RSI is overbought and fast EMA crosses below slow EMA or is about to.
        elif (session_move_pct > 0.5 and # Stretched up move
              current_close > fast_ema and # Currently above fast EMA (shows recent strength)
              rsi > 65 and # Overbought
              fast_ema < slow_ema * 1.005 and # Fast EMA close to or below slow EMA (potential reversal)
              current_close < self.session_high * 0.999 # Not making new session highs aggressively
             ):
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = 0.75
                reason = "session_overnight_fade_short_stretched_up"

        if signal_direction:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open_price": self.session_open_price,
                    "session_move_pct": session_move_pct,
                    "current_close": current_close,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "rsi": rsi,
                    "regime": regime,
                    "trend_regime": trend_regime,
                    "fear_greed_index": fear_greed_index,
                    "funding_rate": funding_rate
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis or adaptive sizing
        pass