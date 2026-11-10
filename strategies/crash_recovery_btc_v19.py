from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicCapitulationRecovery(Strategy):
    METADATA = {
        "name": "PanicCapitulationRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.045  # Relaxed slightly from 6% to 4.5% to ensure adequate sample size on BTC 1h
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.max_hold_bars = 12
        self.entry_bar = -1
        self.flush_high = 0.0
        self.flush_low = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position Management
        if ctx.has_position():
            bars_held = current_bar - self.entry_bar
            midpoint = (self.flush_high + self.flush_low) / 2.0 if self.flush_high > 0 else 0.0

            # Exit on reaching the 24-bar recovery midpoint
            if midpoint > 0 and current_close >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "midpoint": round(midpoint, 2),
                        "close": round(current_close, 2),
                        "bars_held": bars_held
                    }
                )

            # Time-based stop to avoid getting trapped in secular downtrends
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_bars_held_elapsed",
                        "close": round(current_close, 2),
                        "bars_held": bars_held
                    }
                )

            return None

        # Mandatory cooldown check
        if (current_bar - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate rolling 24-bar return
        ref_close = closes[-self.lookback]
        rolling_return = (current_close - ref_close) / ref_close

        # Capitulation condition:
        # 1. 24-bar drop exceeds flush threshold
        # 2. Reversal confirmation: current close > previous bar's high
        prev_high = highs[-2]
        reversal_candle = current_close > prev_high

        if rolling_return <= self.flush_threshold_pct and reversal_candle:
            recent_rsi = self._rsi(closes, period=14)
            # Require oversold or resetting momentum (RSI < 45) to avoid false late-stage re-entries
            if recent_rsi < 45.0:
                self.entry_bar = current_bar
                self.flush_high = max(highs[-self.lookback:])
                self.flush_low = min(lows[-self.lookback:])

                fg_index = ctx.features.get("fear_greed_index", 50.0)
                # Calibrate confidence based on severity of the flush
                confidence = min(0.9, 0.6 + abs(rolling_return) * 2.0)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "panic_capitulation_engulfing_reversal",
                        "rolling_24h_return_pct": round(rolling_return * 100, 2),
                        "rsi": round(recent_rsi, 2),
                        "fear_greed": fg_index,
                        "flush_high": round(self.flush_high, 2),
                        "flush_low": round(self.flush_low, 2)
                    }
                )

        return None