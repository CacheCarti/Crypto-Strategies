from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionFadeSOL(Strategy):
    METADATA = {
        "name": "USSessionFadeSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,       # Stop loss for a swing fade
        "declared_tp_bps": 400.0,       # Take profit for a swing fade
        "declared_hold_seconds": 3600 * 4, # Intended avg hold time (4 hours max)
        "warmup_bars": 50,              # Need enough bars for EMA and ATR
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 13  # UTC 13:00
        self.session_end_hour = 21    # UTC 21:00 (exit before this bar closes)
        self.fade_period = 4          # Lookback for session move (4 hours)
        self.ema_period = 20          # EMA for trend filtering
        self.atr_period = 14          # ATR for volatility
        self.cooldown_bars = 6        # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Exit logic for open position
        if ctx.has_position():
            # Exit if session is ending or past the intended hold time
            if current_hour >= self.session_end_hour or ctx.bar_index - self.last_trade_bar > (self.declared_hold_seconds / 3600) + 1:
                direction = ctx.position_direction()
                entry_price = ctx.position["entry_price"]
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_or_hold_time_exceeded",
                    "current_hour": current_hour,
                    "session_end_hour": self.session_end_hour,
                    "bars_held": ctx.bar_index - self.last_trade_bar,
                    "entry_price": entry_price,
                    "current_price": current_close,
                    "direction": direction
                })
            return None # Hold position

        # Cooldown period after a previous trade
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider entries during the US session
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # Calculate indicators
        ema = self._ema(closes, self.ema_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema is None or atr is None:
            return None

        # Regime filters
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Avoid trading in strong trends or crisis
        if trend_regime != "neutral" or crisis_score > 0.3 or fear_greed_index < 30:
            return None

        # Calculate session move
        session_closes = closes[-self.fade_period:]
        session_opens = ctx.opens(self.fade_period)[0] # First open of the lookback
        
        if len(session_closes) < self.fade_period:
            return None

        session_range = max(session_closes) - min(session_closes)
        if session_range == 0:
            return None # Avoid division by zero

        # Check for unusually stretched move
        # We define "stretched" as a move exceeding 1.5 * ATR in the past few bars
        # And the current price is significantly away from the session_opens
        move_magnitude = abs(current_close - session_opens)
        is_stretched = move_magnitude > (1.5 * atr)

        # Check for stalling (price action slowing down or reversing slightly)
        # This is a simple check: current close is near the extreme of the recent range
        # but not continuing strongly in that direction
        # For long fade: price is high, but last bar close is below last high
        # For short fade: price is low, but last bar close is above last low
        stalling_long = current_close > ema and (closes[-1] < closes[-2] or closes[-1] < highs[-2])
        stalling_short = current_close < ema and (closes[-1] > closes[-2] or closes[-1] > lows[-2])

        # Fade logic
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long fade (short entry): price is stretched upwards, stalling, and above EMA
        if is_stretched and stalling_long and current_close > ema:
            # Add funding rate as a filter for shorting: prefer positive funding
            if funding_rate > 0.0001: # Positive funding rate (payers pay shorts)
                signal_direction = "short"
                confidence = min(0.7, move_magnitude / (2 * atr)) # Scale confidence by stretch
                reason = "us_session_fade_short_stretched_stalling_above_ema_positive_funding"

        # Short fade (long entry): price is stretched downwards, stalling, and below EMA
        elif is_stretched and stalling_short and current_close < ema:
            # Add funding rate as a filter for longing: prefer negative funding
            if funding_rate < -0.0001: # Negative funding rate (payers pay longs)
                signal_direction = "long"
                confidence = min(0.7, move_magnitude / (2 * atr)) # Scale confidence by stretch
                reason = "us_session_fade_long_stretched_stalling_below_ema_negative_funding"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_start_hour": self.session_start_hour,
                    "session_end_hour": self.session_end_hour,
                    "current_close": current_close,
                    "session_opens_lookback": session_opens,
                    "move_magnitude_from_open": move_magnitude,
                    "is_stretched": is_stretched,
                    "stalling_long": stalling_long,
                    "stalling_short": stalling_short,
                    "ema": ema,
                    "atr": atr,
                    "trend_regime": trend_regime,
                    "crisis_score": crisis_score,
                    "fear_greed_index": fear_greed_index,
                    "funding_rate_solusdt": funding_rate
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log for debugging/analysis
        pass