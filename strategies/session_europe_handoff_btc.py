from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEuMorningImpulse(Strategy):
    METADATA = {
        "name": "BTC EU Morning Impulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 3600 * 5,  # Max hold is until 12 UTC, ~5 hours if entered at 7 UTC
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7
        self.entry_hour_end = 9    # UTC 9 (exclusive, so 7, 8)
        self.exit_hour = 12        # UTC 12
        self.cooldown_bars = 6     # Cooldown for 6 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars
        self.entry_price = 0.0
        self.entry_direction = None
        self.ema_fast_period = 8
        self.ema_slow_period = 21
        self.rsi_period = 14
        self.vol_sma_period = 10
        self.stddev_period = 20

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
        
        # Calculate initial average gain/loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        volumes = ctx.volumes(self.vol_sma_period)
        
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Exit logic: Close all positions by 12 UTC
        if ctx.has_position() and current_hour >= self.exit_hour and self.entry_direction is not None:
            reason_meta = {"reason": "session_handoff_exit", "entry_hour": self.entry_hour_start, "exit_hour": self.exit_hour}
            if self.entry_price > 0: # Ensure entry_price is set
                if ctx.position_direction() == "long":
                    reason_meta["pnl_bps_since_entry"] = (ctx.bar.close - self.entry_price) / self.entry_price * 10000
                else: # short
                    reason_meta["pnl_bps_since_entry"] = (self.entry_price - ctx.bar.close) / self.entry_price * 10000
            return ctx.signal("flat", confidence=0.8, metadata=reason_meta)

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Entry logic: Only enter between 7-9 UTC (inclusive of 7, 8, exclusive of 9)
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Ensure we only attempt one entry per session
        if self.entry_direction is not None and self.entry_hour_start <= current_hour < self.exit_hour:
             return None # Already entered this session

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)
        vol_sma = self._sma(volumes, self.vol_sma_period)
        std_dev = self._stddev(closes, self.stddev_period)

        if ema_fast is None or ema_slow is None or rsi is None or vol_sma is None or std_dev is None:
            return None

        # Market regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        is_volatile = std_dev > (self._sma(ctx.highs(self.stddev_period), self.stddev_period) - self._sma(ctx.lows(self.stddev_period), self.stddev_period)) / 2 # Simple proxy for volatility based on ATR-like range
        
        # Don't trade if too volatile or extreme fear/greed
        if is_volatile and ctx.rng.uniform(0, 1) < 0.3: # Randomly skip some trades if volatile
            return None
        if fear_greed_index <= 20 or fear_greed_index >= 80: # Extreme fear/greed
            return None

        # Entry conditions
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Bullish impulse: Fast EMA crosses above Slow EMA, RSI not overbought, above average volume
        if ema_fast > ema_slow and closes[-2] <= ema_slow and rsi < 70 and ctx.bar.volume > vol_sma * 1.2:
            signal_direction = "long"
            confidence = 0.75
            reason = "eu_morning_bullish_impulse_ema_cross_vol_confirm"
        
        # Bearish impulse: Fast EMA crosses below Slow EMA, RSI not oversold, above average volume
        elif ema_fast < ema_slow and closes[-2] >= ema_slow and rsi > 30 and ctx.bar.volume > vol_sma * 1.2:
            signal_direction = "short"
            confidence = 0.75
            reason = "eu_morning_bearish_impulse_ema_cross_vol_confirm"

        if signal_direction and not ctx.has_position():
            self.last_trade_bar_index = ctx.bar_index
            self.entry_price = ctx.bar.close
            self.entry_direction = signal_direction # Mark that we've entered for this session

            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "rsi": rsi,
                    "volume_ratio": ctx.bar.volume / vol_sma,
                    "fear_greed_index": fear_greed_index,
                    "std_dev": std_dev
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset entry tracking for the next session
        self.entry_direction = None
        self.entry_price = 0.0
        # Optionally, log the outcome or adapt parameters
        # ctx.log(f"Position closed: {position['direction']} return {position['return_bps']} bps, held for {position['bars_held']} bars")