from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthUsSessionFade(Strategy):
    METADATA = {
        "name": "ETH US Session Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max hold until 21 UTC, 13:00 to 21:00 is 8 hours = 28800 seconds.
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC
        self.us_session_end_hour = 21   # UTC (exit before this hour ends)
        self.session_midpoint_bars = 4  # 4 hours into session (13+4 = 17 UTC)
        self.entry_cooldown_bars = 12   # Cooldown for 12 hours after a trade
        self.last_entry_bar_index = -self.entry_cooldown_bars * 2 # Initialize far in past

        self.long_ema_period = 20
        self.short_ema_period = 5
        self.rsi_period = 14
        self.atr_period = 14
        self.min_session_range_mult = 1.5 # Session range must be 1.5x avg ATR
        self.fade_threshold_mult = 1.5 # Price must be 1.5x std dev from session open

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
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

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Exit existing position before session end
        if ctx.has_position() and current_hour == self.us_session_end_hour - 1 and ctx.bar.timestamp.minute == 55:
            # Exit 5 minutes before the session end hour
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Cooldown check
        if ctx.bar_index - self.last_entry_bar_index < self.entry_cooldown_bars:
            return None

        # Only consider entries during US session after midpoint
        if not (self.us_session_start_hour <= current_hour < self.us_session_end_hour):
            return None
        
        # Only enter after the session midpoint
        if current_hour < self.us_session_start_hour + self.session_midpoint_bars:
            return None

        # Calculate session statistics
        session_bars_count = current_hour - self.us_session_start_hour + 1
        if session_bars_count < self.session_midpoint_bars: # Ensure enough bars for session context
            return None

        # Find session open price
        session_open_price = None
        for i in range(len(ctx.opens(session_bars_count))):
            bar_time = ctx.bar.timestamp - (session_bars_count - 1 - i) * (ctx.bar.timestamp - (ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0))) # Approximate previous bar timestamps
            if bar_time.hour == self.us_session_start_hour and bar_time.day == current_day:
                session_open_price = ctx.opens(session_bars_count)[i]
                break
        
        if session_open_price is None:
            return None # Could not find session open within current bars

        # Calculate session range
        session_high = max(highs[-session_bars_count:])
        session_low = min(lows[-session_bars_count:])
        session_range = session_high - session_low

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Regime filtering
        market_regime = ctx.market.get("regime", "NORMAL")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Avoid trading in crisis or strong confirmed trends
        if market_regime in ["CRISIS", "MELTDOWN"] or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None
        
        # Calculate EMAs for trend filter
        ema_short = self._ema(closes, self.short_ema_period)
        ema_long = self._ema(closes, self.long_ema_period)
        if ema_short is None or ema_long is None:
            return None

        # Avoid fading strong trends
        if trend_regime == "bull" and ema_short > ema_long * 1.005: # Price significantly above long EMA
            return None
        if trend_regime == "bear" and ema_short < ema_long * 0.995: # Price significantly below long EMA
            return None

        # Conditions for fading:
        # 1. Session range is unusually stretched
        # 2. Price is significantly far from session open
        # 3. RSI indicates overbought/oversold
        # 4. Not in a position yet

        if ctx.has_position():
            return None

        # Check if session range is stretched
        if session_range < current_atr * self.min_session_range_mult:
            return None
        
        # Calculate standard deviation of recent price moves from session open
        price_deviation = abs(ctx.bar.close - session_open_price)
        recent_stddev = self._stddev(closes[-session_bars_count:], session_bars_count)
        
        if recent_stddev is None or recent_stddev == 0:
            return None

        fade_long_condition = False
        fade_short_condition = False
        
        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        # Fade long: Price stretched significantly below session open, RSI oversold
        if ctx.bar.close < session_open_price - recent_stddev * self.fade_threshold_mult and current_rsi < 30:
            fade_long_condition = True
        
        # Fade short: Price stretched significantly above session open, RSI overbought
        if ctx.bar.close > session_open_price + recent_stddev * self.fade_threshold_mult and current_rsi > 70:
            fade_short_condition = True

        signal_confidence = 0.0
        signal_direction = "hold"
        reason = "no_signal"
        
        if fade_long_condition:
            signal_direction = "long"
            signal_confidence = min(0.7, 0.4 + (session_range / (current_atr * 5))) # Higher range, higher confidence
            reason = "session_fade_long_oversold"
            self.last_entry_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open": session_open_price,
                    "current_close": ctx.bar.close,
                    "rsi": current_rsi,
                    "session_range": session_range,
                    "avg_atr": current_atr,
                    "price_deviation_from_open": price_deviation,
                    "recent_stddev": recent_stddev,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "market_regime": market_regime,
                    "fear_greed_index": fear_greed_index,
                }
            )

        elif fade_short_condition:
            signal_direction = "short"
            signal_confidence = min(0.7, 0.4 + (session_range / (current_atr * 5))) # Higher range, higher confidence
            reason = "session_fade_short_overbought"
            self.last_entry_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open": session_open_price,
                    "current_close": ctx.bar.close,
                    "rsi": current_rsi,
                    "session_range": session_range,
                    "avg_atr": current_atr,
                    "price_deviation_from_open": price_deviation,
                    "recent_stddev": recent_stddev,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "market_regime": market_regime,
                    "fear_greed_index": fear_greed_index,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Reset last_entry_bar_index to allow faster re-entry after a successful close
        # For now, keep the cooldown as is.
        pass