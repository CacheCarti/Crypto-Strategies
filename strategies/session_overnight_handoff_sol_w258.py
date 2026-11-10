from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightImpulseCarry(Strategy):
    METADATA = {
        "name": "Overnight Impulse Carry SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for up to 6 hours hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar = -self.entry_cooldown_bars
        self.overnight_entry_hour_start = 0  # UTC 0 (midnight)
        self.overnight_entry_hour_end = 6    # UTC 6 (6 AM)
        self.exit_hour = 12                  # UTC 12 (noon)
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.rsi_period = 14
        self.atr_period = 14
        self.atr_multiplier = 1.5 # For dynamic stop loss

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
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
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: Close all positions at self.exit_hour
        if ctx.has_position() and current_hour == self.exit_hour:
            direction = ctx.position_direction()
            return ctx.signal("flat", confidence=0.9, metadata={"reason": f"time_based_exit_{direction}", "exit_hour": self.exit_hour})

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # Only consider entries during the overnight window
        if not (self.overnight_entry_hour_start <= current_hour < self.overnight_entry_hour_end):
            return None

        # Calculate indicators
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        atr_val = self._atr(highs, lows, closes, self.atr_period)

        if ema_short is None or ema_long is None or rsi_val is None or atr_val is None:
            return None

        # Regime filters
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        
        # Determine dynamic stop loss based on ATR
        dynamic_sl_bps = (atr_val / ctx.bar.close) * 10000 * self.atr_multiplier
        # Ensure SL is within reasonable bounds
        dynamic_sl_bps = max(self.METADATA["declared_sl_bps"] * 0.75, min(dynamic_sl_bps, self.METADATA["declared_sl_bps"] * 1.5))

        signal = None
        current_price = ctx.bar.close

        # Long Entry Conditions:
        # 1. Short EMA above Long EMA (bullish trend)
        # 2. RSI is not overbought (below 70)
        # 3. Funding rate is not excessively negative (avoid strong short pressure)
        # 4. Fear & Greed not in extreme greed
        if not ctx.has_position():
            if ema_short > ema_long and \
               rsi_val < 70 and \
               funding_rate > -0.0001 and \
               fear_greed < 75:
                signal = ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_impulse_long",
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "rsi": rsi_val,
                        "funding_rate": funding_rate,
                        "fear_greed": fear_greed,
                        "sl_bps_calculated": dynamic_sl_bps
                    }
                )

            # Short Entry Conditions:
            # 1. Short EMA below Long EMA (bearish trend)
            # 2. RSI is not oversold (above 30)
            # 3. Funding rate is not excessively positive (avoid strong long pressure)
            # 4. Fear & Greed not in extreme fear
            elif ema_short < ema_long and \
                 rsi_val > 30 and \
                 funding_rate < 0.0001 and \
                 fear_greed > 25:
                signal = ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_impulse_short",
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "rsi": rsi_val,
                        "funding_rate": funding_rate,
                        "fear_greed": fear_greed,
                        "sl_bps_calculated": dynamic_sl_bps
                    }
                )

        if signal:
            self.last_trade_bar = ctx.bar_index
            return signal

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You could implement adaptive sizing or other logic here.
        # For simplicity, we just reset the cooldown.
        self.last_trade_bar = ctx.bar_index
        # print(f"Position closed: {position['direction']} at {position['exit_price']} for {position['return_bps']} bps return.")