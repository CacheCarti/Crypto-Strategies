from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightFadeBTC(Strategy):
    METADATA = {
        "name": "Overnight_Fade_BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown = 0
        self.max_cooldown = 10 # Wait 10 bars (10 hours) after a trade
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.session_length = 6 # 0-6 UTC
        self.prev_day = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
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
            return 100.0 if avg_gain > 0 else 50.0 # Prevent division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update cooldown
        if self.entry_cooldown > 0:
            self.entry_cooldown -= 1
            return None

        # Reset session high/low/volume at the start of the session (UTC 0)
        if current_hour == 0 and current_day != self.prev_day:
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_volume = ctx.bar.volume
            self.prev_day = current_day
            self.session_bars = 1
            self.session_closes = [ctx.bar.close]
            return None # Don't trade on the very first bar of the session

        if current_day == self.prev_day: # Within the same session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_volume += ctx.bar.volume
            self.session_closes.append(ctx.bar.close)
            self.session_bars += 1

        # Only trade within the overnight window (0-6 UTC) and not at the very start or end
        if not (0 < current_hour < self.session_length):
            return None

        # Calculate session midpoint and range
        session_range = self.session_high - self.session_low
        if session_range == 0:
            return None
        session_midpoint = (self.session_high + self.session_low) / 2.0

        # Calculate percentage move from session open
        session_move_pct = (ctx.bar.close - self.session_open) / self.session_open * 100.0

        # Indicators for trend and momentum confirmation
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        
        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Do not fade strong trends or crisis regimes
        if market_regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"]:
             return None

        # Do not trade if fear/greed is extreme or funding is very high/low (indicating strong sentiment)
        if fear_greed < 20 or fear_greed > 80 or funding_rate > 0.0005 or funding_rate < -0.0005:
            return None

        # Entry logic: Fade stretched moves towards session midpoint, only if price stalls
        # Long condition: Price significantly below session open, near session low, but above midpoint.
        #                  RSI is oversold but not extremely so, and showing signs of bottoming (e.g., rising).
        # Short condition: Price significantly above session open, near session high, but below midpoint.
        #                   RSI is overbought but not extremely so, and showing signs of topping (e.g., falling).
        
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Fade short (go long)
        if not ctx.has_position() and ctx.bar.close < self.session_open * (1 - 0.005) and \
           ctx.bar.close > session_midpoint and \
           (ctx.bar.close - self.session_low) / session_range < 0.3 and \
           rsi < 40 and fast_ema > slow_ema: # RSI oversold, price below open, but above midpoint, and trend is up
            
            # Check for stalling: current bar's close is higher than previous close, or small candle
            if len(closes) >= 2 and closes[-1] > closes[-2] or abs(closes[-1] - opens[-1]) / closes[-1] < 0.003:
                signal_direction = "long"
                confidence = min(0.7, abs(session_move_pct) * 0.1 + (40 - rsi) / 40.0) # Scale confidence
                reason = "overnight_fade_long_stall"

        # Fade long (go short)
        elif not ctx.has_position() and ctx.bar.close > self.session_open * (1 + 0.005) and \
             ctx.bar.close < session_midpoint and \
             (self.session_high - ctx.bar.close) / session_range < 0.3 and \
             rsi > 60 and fast_ema < slow_ema: # RSI overbought, price above open, but below midpoint, and trend is down

            # Check for stalling: current bar's close is lower than previous close, or small candle
            if len(closes) >= 2 and closes[-1] < closes[-2] or abs(closes[-1] - opens[-1]) / closes[-1] < 0.003:
                signal_direction = "short"
                confidence = min(0.7, abs(session_move_pct) * 0.1 + (rsi - 60) / 40.0) # Scale confidence
                reason = "overnight_fade_short_stall"

        if signal_direction:
            self.entry_cooldown = self.max_cooldown # Activate cooldown
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open": self.session_open,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_midpoint": session_midpoint,
                    "session_move_pct": session_move_pct,
                    "rsi": rsi,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "fear_greed_index": fear_greed,
                    "funding_rate": funding_rate,
                },
            )
        
        # Exit logic: If current position is open, check for exit conditions
        if ctx.has_position():
            # Exit if approaching the end of the overnight session (before 7 UTC)
            if current_hour >= self.session_length - 1: # Exit in the 5th hour (0-indexed) to avoid holding into next session
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "session_open": self.session_open,
                        "session_high": self.session_high,
                        "session_low": self.session_low,
                        "rsi": rsi,
                    },
                )
            
            # Additional exit condition: if the initial fade idea is invalidated by strong trend continuation
            if ctx.position_direction() == "long" and ctx.bar.close < slow_ema * (1 - 0.005) and rsi < 30: # Price breaks down below slow EMA and becomes extremely oversold
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_fade_breakdown_exit",
                        "current_hour": current_hour,
                        "rsi": rsi,
                        "slow_ema": slow_ema,
                        "close": ctx.bar.close,
                    },
                )
            elif ctx.position_direction() == "short" and ctx.bar.close > slow_ema * (1 + 0.005) and rsi > 70: # Price breaks up above slow EMA and becomes extremely overbought
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_fade_breakup_exit",
                        "current_hour": current_hour,
                        "rsi": rsi,
                        "slow_ema": slow_ema,
                        "close": ctx.bar.close,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close to ensure we can re-enter if conditions permit
        # Or, implement adaptive cooldown based on win/loss
        if position["return_bps"] < 0: # If it was a losing trade, increase cooldown
            self.entry_cooldown = int(self.max_cooldown * 1.5)
        else:
            self.entry_cooldown = self.max_cooldown
        pass # No complex adaptive sizing for now