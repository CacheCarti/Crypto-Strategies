from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyCloseBTC(Strategy):
    METADATA = {
        "name": "Weekly Close BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 120000, # ~33 hours, Fri evening to Sun evening
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.rsi_period = 14
        self.volume_ma_period = 20
        self.last_signal_bar = -self.warmup_bars # Initialize to allow immediate trading after warmup
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for the initial EMA
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

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Calculate indicators
        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        volume_ma = self._sma(volumes, self.volume_ma_period)

        if ema is None or rsi is None or volume_ma is None:
            return None

        # --- Entry Logic ---
        signal_direction = None
        signal_confidence = 0.0
        signal_reason = ""
        signal_metadata = {}

        # Weekly close strategy: Friday evening to Saturday morning UTC for BTC
        # Look for decisive moves in the last few hours before Friday's close (UTC)
        # Friday (weekday 4), hours 18-23 UTC (last 6 hours of Friday UTC)
        # We need enough bars to check for a strong trend into the close
        if current_weekday == 4 and current_hour >= 18:
            # Check price momentum into the close (e.g., last 6 bars)
            lookback_bars = 6
            if len(closes) < self.warmup_bars + lookback_bars:
                return None

            recent_closes = closes[-lookback_bars:]
            recent_volumes = volumes[-lookback_bars:]

            # Price change over the lookback period
            price_change = (recent_closes[-1] - recent_closes[0]) / recent_closes[0]

            # Average volume over the lookback period
            avg_recent_volume = sum(recent_volumes) / lookback_bars
            volume_ratio = avg_recent_volume / volume_ma if volume_ma > 0 else 1.0

            # Funding rate as a sentiment filter
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
            fear_greed = ctx.features.get("fear_greed_index", 50)

            # Long condition: Strong upward momentum into Friday close with high volume
            if price_change > 0.005 and volume_ratio > 1.2 and rsi < 70 and funding_rate < 0.0001: # 0.5% gain, 20% higher volume
                if not ctx.has_position():
                    signal_direction = "long"
                    signal_confidence = min(0.7 + (volume_ratio - 1.2) * 0.1, 0.9) # Higher volume, higher confidence
                    signal_reason = "friday_close_bullish_momentum_high_volume"
                    signal_metadata = {
                        "price_change_pct": price_change * 100,
                        "volume_ratio": volume_ratio,
                        "current_rsi": rsi,
                        "funding_rate": funding_rate,
                        "fear_greed_index": fear_greed,
                        "entry_hour": current_hour,
                        "entry_weekday": current_weekday
                    }
            # Short condition: Strong downward momentum into Friday close with high volume
            elif price_change < -0.005 and volume_ratio > 1.2 and rsi > 30 and funding_rate > -0.0001:
                if not ctx.has_position():
                    signal_direction = "short"
                    signal_confidence = min(0.7 + (volume_ratio - 1.2) * 0.1, 0.9)
                    signal_reason = "friday_close_bearish_momentum_high_volume"
                    signal_metadata = {
                        "price_change_pct": price_change * 100,
                        "volume_ratio": volume_ratio,
                        "current_rsi": rsi,
                        "funding_rate": funding_rate,
                        "fear_greed_index": fear_greed,
                        "entry_hour": current_hour,
                        "entry_weekday": current_weekday
                    }

        # --- Exit Logic (if not already handled by declared SL/TP) ---
        # Exit on Sunday evening UTC (Sunday is 6, exit around 18-23 UTC)
        if ctx.has_position() and current_weekday == 6 and current_hour >= 18:
            # We want to flatten the position
            signal_direction = "flat"
            signal_confidence = 0.8 # High confidence to close on schedule
            signal_reason = "sunday_evening_scheduled_exit"
            signal_metadata = {
                "exit_hour": current_hour,
                "exit_weekday": current_weekday,
                "entry_direction": ctx.position["direction"],
                "entry_price": ctx.position["entry_price"],
                "current_price": ctx.bar.close,
            }

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=signal_metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt future behavior
        # For this strategy, we just ensure cooldown is applied.
        pass