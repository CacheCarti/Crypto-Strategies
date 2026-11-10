from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TokyoHandoffContinuation(Strategy):
    METADATA = {
        "name": "Tokyo Handoff Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600, # 6 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24
        self.cooldown_bars = 6 # Wait 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.min_tokyo_move_pct = 0.5 # 0.5% move
        self.max_tokyo_move_pct = 3.0 # Not too exhausted
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.ema_fast_period = 10
        self.ema_slow_period = 20

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = self._sma(values[:period], period) # Use SMA for initial value
        if ema is None:
            return None
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

        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_loss == 0:
            return 100.0
        if avg_gain == 0:
            return 0.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period)
        highs = ctx.highs(self.lookback_period)
        lows = ctx.lows(self.lookback_period)

        if len(closes) < self.lookback_period or ctx.bar_index < self.warmup_bars:
            return None

        if ctx.has_position():
            return None # Hold existing position

        # Cooldown period check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Define Tokyo session (UTC 0-8)
        # We are looking for entry opportunities at the end of Tokyo session (hour 7 or 8)
        # to carry into Europe/US handoff.
        # Tokyo session closes at 8 UTC, so we evaluate at 7 UTC (the bar ending at 8 UTC)
        if current_hour != 7: # Evaluate at 7 AM UTC (bar 07:00-08:00 UTC)
            return None

        # Calculate Tokyo session move (bars 0 UTC to 8 UTC, which means 9 bars total)
        # We need data from the start of the Tokyo session (0 UTC) up to current bar (7 UTC)
        # So we need closes from ctx.closes(current_hour + 1)
        # For a bar ending at 7 UTC, the bar from 0 UTC to 1 UTC is at index -current_hour-1
        
        # Ensure we have enough bars for the Tokyo session
        # The bar at 0 UTC is closes[-current_hour-1]
        # The bar at 7 UTC (current bar) is closes[-1]
        if len(closes) < current_hour + 1:
            return None

        tokyo_session_opens = ctx.opens(current_hour + 1)
        tokyo_session_closes = ctx.closes(current_hour + 1)
        tokyo_session_highs = ctx.highs(current_hour + 1)
        tokyo_session_lows = ctx.lows(current_hour + 1)

        tokyo_open = tokyo_session_opens[0]
        tokyo_close = tokyo_session_closes[-1]
        tokyo_high = max(tokyo_session_highs)
        tokyo_low = min(tokyo_session_lows)

        if tokyo_open == 0: # Avoid division by zero
            return None

        tokyo_move_pct = ((tokyo_close - tokyo_open) / tokyo_open) * 100
        tokyo_range_pct = ((tokyo_high - tokyo_low) / tokyo_open) * 100

        # Conditions for entry
        # 1. Decisive Tokyo move within a reasonable range
        # 2. RSI not overbought/oversold (not exhausted)
        # 3. Price above/below EMA for trend confirmation
        # 4. Moderate Fear & Greed index (not extreme)
        # 5. Funding rate not excessively positive/negative (less likely to be a squeeze)

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry conditions
        if (self.min_tokyo_move_pct < tokyo_move_pct < self.max_tokyo_move_pct and # Positive move, not too small, not too large
            current_rsi < self.rsi_overbought and # Not overbought
            closes[-1] > ema_fast and closes[-1] > ema_slow and # Price above EMAs
            ema_fast > ema_slow and # EMAs are in bullish order
            30 <= fear_greed_index <= 70 and # Not extreme fear or greed
            funding_rate < 0.0005): # Funding rate not excessively high
            signal_direction = "long"
            confidence = 0.7 + (tokyo_move_pct / self.max_tokyo_move_pct) * 0.2 # Scale confidence by move size
            reason = "tokyo_bullish_handoff_ema_confirm_moderate_regime"

        # Short entry conditions
        elif ( -self.max_tokyo_move_pct < tokyo_move_pct < -self.min_tokyo_move_pct and # Negative move, not too small, not too large
               current_rsi > self.rsi_oversold and # Not oversold
               closes[-1] < ema_fast and closes[-1] < ema_slow and # Price below EMAs
               ema_fast < ema_slow and # EMAs are in bearish order
               30 <= fear_greed_index <= 70 and # Not extreme fear or greed
               funding_rate > -0.0005): # Funding rate not excessively low
            signal_direction = "short"
            confidence = 0.7 + (abs(tokyo_move_pct) / self.max_tokyo_move_pct) * 0.2
            reason = "tokyo_bearish_handoff_ema_confirm_moderate_regime"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence),
                metadata={
                    "reason": reason,
                    "tokyo_move_pct": round(tokyo_move_pct, 2),
                    "tokyo_range_pct": round(tokyo_range_pct, 2),
                    "current_rsi": round(current_rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": round(closes[-1], 2),
                    "fear_greed_index": fear_greed_index,
                    "funding_rate_btcusdt": round(funding_rate, 5)
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not used for adaptive logic in this strategy, but good to have for logging.
        pass