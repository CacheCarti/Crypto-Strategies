from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEuropeSessionFade(Strategy):
    METADATA = {
        "name": "BTC Europe Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 300.0,
        "declared_hold_seconds": 3600 * 3, # Max hold time for session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7:00
        self.entry_hour_end = 10   # UTC 10:00 (allowing entry up to and including 10:00 bar)
        self.exit_hour = 12        # UTC 12:00 (exit before this bar)
        self.session_lookback_period = 4 # bars to check for session stretch (e.g., 4 hours)
        self.fade_threshold_pct = 0.5 # % away from session open to consider "stretched"
        self.volatility_threshold = 0.0005 # ATR % threshold for high vol regime
        self.cooldown_bars = 5 # bars to wait after closing a position

        self.last_trade_bar = -self.cooldown_bars
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.traded_this_session = False

        # Parameters for indicators
        self.ema_period = 20
        self.rsi_period = 14
        self.atr_period = 14

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
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need enough for EMA/RSI/ATR
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Daily reset logic for session tracking
        if current_hour == self.entry_hour_start and not self.traded_this_session:
            # New session starts, reset
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.traded_this_session = False
        elif current_hour == self.entry_hour_start and self.traded_this_session:
             # Already traded this session, still update session high/low
            self.session_high = max(self.session_high, ctx.bar.high) if self.session_high else ctx.bar.high
            self.session_low = min(self.session_low, ctx.bar.low) if self.session_low else ctx.bar.low
        elif current_hour > self.entry_hour_start and current_hour <= self.exit_hour:
             # During session, update high/low
            self.session_high = max(self.session_high, ctx.bar.high) if self.session_high else ctx.bar.high
            self.session_low = min(self.session_low, ctx.bar.low) if self.session_low else ctx.bar.low
        elif current_hour > self.exit_hour:
            # After session, reset for next day
            self.session_open_price = None
            self.session_high = None
            self.session_low = None
            self.traded_this_session = False


        # Exit logic for existing position
        if ctx.has_position():
            if current_hour >= self.exit_hour:
                self.last_trade_bar = ctx.bar_index
                self.traded_this_session = True # Mark as traded for this session
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_time", "hour": current_hour})
            return None # Hold position within session

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check if within entry window
        if not (self.entry_hour_start <= current_hour <= self.entry_hour_end):
            return None

        # Ensure session data is available
        if self.session_open_price is None or self.traded_this_session:
            return None

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Do not fade in strong trend or crisis
        if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None
        if fear_greed_index < 30 or fear_greed_index > 70: # Avoid extreme F&G for fading
            return None
        
        # Volatility check - use ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None
        
        # Calculate recent average true range in percentage
        avg_atr_pct = (atr / current_close) if current_close != 0 else 0
        if avg_atr_pct > self.volatility_threshold * 2: # Too volatile to fade
            return None

        # Calculate session stretch
        if self.session_open_price is None:
            return None

        stretch_long = (current_close - self.session_open_price) / self.session_open_price
        stretch_short = (self.session_open_price - current_close) / self.session_open_price

        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Fade Long (price stretched up, look for short)
        if stretch_long > self.fade_threshold_pct and current_close > ema_val and rsi_val > 70:
            if not ctx.has_position():
                signal_direction = "short"
                confidence = 0.7 + (ctx.rng.uniform(0, 0.1) if abs(funding_rate) > 0.0002 else 0) # Higher confidence if funding is positive (longs paying shorts)
                reason = "session_stretched_up_ema_overbought"
        
        # Fade Short (price stretched down, look for long)
        elif stretch_short > self.fade_threshold_pct and current_close < ema_val and rsi_val < 30:
            if not ctx.has_position():
                signal_direction = "long"
                confidence = 0.7 + (ctx.rng.uniform(0, 0.1) if abs(funding_rate) < -0.0002 else 0) # Higher confidence if funding is negative (shorts paying longs)
                reason = "session_stretched_down_ema_oversold"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            self.traded_this_session = True # Mark as traded for this session
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "session_open": self.session_open_price,
                    "current_close": current_close,
                    "stretch_long_pct": stretch_long * 100,
                    "stretch_short_pct": stretch_short * 100,
                    "ema_val": ema_val,
                    "rsi_val": rsi_val,
                    "regime": market_regime,
                    "trend": trend_regime,
                    "fg_index": fear_greed_index,
                    "funding_rate": funding_rate,
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_this_session if position was closed, allowing for a new entry in the same session
        # This implementation allows only one trade per session. If we wanted multiple, we'd remove this.
        # For a fade strategy, one attempt per session is usually best.
        pass