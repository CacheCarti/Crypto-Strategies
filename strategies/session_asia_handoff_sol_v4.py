from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolAsiaHandoff(Strategy):
    METADATA = {
        "name": "SOL Asia Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1 hour bar, so ~1-4 bars hold max
        "warmup_bars": 50,
        "required_features": [], # No specific external features used, relying on OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_start_hour = 0  # UTC 00:00
        self.asia_session_end_hour = 8    # UTC 08:00
        self.last_trade_bar_index = -100 # Cooldown period
        self.min_cooldown_bars = 4       # Minimum bars to wait after a trade
        self.ema_period_fast = 10
        self.ema_period_slow = 30
        self.rsi_period = 14
        self.std_dev_period = 20
        self.zscore_threshold = 1.0 # Z-score for volatility filter
        self.min_volatility_multiplier = 0.5 # For regime filtering

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = self._sma(gains[-period-1:-1], period) # Use SMA for initial average
        avg_loss = self._sma(losses[-period-1:-1], period) # Use SMA for initial average

        if avg_gain is None or avg_loss is None:
            return None

        # Wilders smoothing for subsequent periods
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        rsi = self._rsi(closes, self.rsi_period)
        
        # Volatility filter using Z-score of recent price changes
        price_changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        zscore_price_change = self._zscore(price_changes, self.std_dev_period)
        
        # Market regime filter: only trade in 'normal' or 'volatile' conditions, avoid 'crisis'
        market_regime = ctx.market.get("regime", "NORMAL")
        
        # Trend filter: only trade with the trend
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Exit logic
        if ctx.has_position():
            # Always exit before the end of the Asia session handoff if not already out
            if current_hour >= self.asia_session_end_hour:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_handoff_exit", "exit_hour": current_hour})
            
            # Exit on structure stop (EMA cross against position)
            if ctx.position_direction() == "long" and ema_fast < ema_slow and ema_fast is not None and ema_slow is not None:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_ema_cross_exit", "ema_fast": ema_fast, "ema_slow": ema_slow})
            if ctx.position_direction() == "short" and ema_fast > ema_slow and ema_fast is not None and ema_slow is not None:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_ema_cross_exit", "ema_fast": ema_fast, "ema_slow": ema_slow})
            
            return ctx.signal("hold") # Hold if no exit condition met

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.min_cooldown_bars:
            return None

        # Entry logic - only during Asia session (UTC 0-8)
        if not (self.asia_session_start_hour <= current_hour < self.asia_session_end_hour):
            return None
        
        # Ensure all indicators are available
        if any(x is None for x in [ema_fast, ema_slow, rsi, zscore_price_change]):
            return None

        # Regime filters
        if market_regime == "CRISIS" or market_regime == "MELTDOWN":
            return None # Stand down in extreme market conditions

        # Volatility filter: ensure sufficient market movement (not too flat)
        # We want to catch the impulse, so we need some volatility.
        # Too high zscore could mean an extreme move already happened,
        # too low means no impulse.
        if abs(zscore_price_change) < 0.5: # Example threshold
             return None

        signal_confidence = 0.0
        signal_direction = None
        reason = ""
        
        # Long entry conditions:
        # 1. Price is above slow EMA, indicating bullish trend
        # 2. RSI is not overbought (below 70)
        # 3. EMA fast crosses above EMA slow, or is already above and trending up
        # 4. Market trend is bullish or neutral with confidence
        if closes[-1] > ema_slow and rsi < 70 and ema_fast > ema_slow:
            if trend_regime == "bull" and trend_confidence > 0.5:
                signal_direction = "long"
                signal_confidence = 0.75
                reason = "asia_session_long_bull_trend_ema_cross_above_rsi_ok"
            elif trend_regime == "neutral" and trend_confidence > 0.3:
                signal_direction = "long"
                signal_confidence = 0.65
                reason = "asia_session_long_neutral_trend_ema_cross_above_rsi_ok"

        # Short entry conditions:
        # 1. Price is below slow EMA, indicating bearish trend
        # 2. RSI is not oversold (above 30)
        # 3. EMA fast crosses below EMA slow, or is already below and trending down
        # 4. Market trend is bearish or neutral with confidence
        elif closes[-1] < ema_slow and rsi > 30 and ema_fast < ema_slow:
            if trend_regime == "bear" and trend_confidence > 0.5:
                signal_direction = "short"
                signal_confidence = 0.75
                reason = "asia_session_short_bear_trend_ema_cross_below_rsi_ok"
            elif trend_regime == "neutral" and trend_confidence > 0.3:
                signal_direction = "short"
                signal_confidence = 0.65
                reason = "asia_session_short_neutral_trend_ema_cross_below_rsi_ok"

        if signal_direction:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "rsi": rsi,
                    "zscore_price_change": zscore_price_change,
                    "market_regime": market_regime,
                    "trend_regime": trend_regime,
                    "trend_confidence": trend_confidence,
                    "price": closes[-1]
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing event for analysis
        pass # No adaptive sizing or state reset needed beyond cooldown