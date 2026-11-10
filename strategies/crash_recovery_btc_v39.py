from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBtc(Strategy):
    METADATA = {
        "name": "BTC Capitulation Flush Bounce",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.05  # -5.0% rolling return over 24h
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = closes[-1]
        prev_high = highs[-2]
        
        # Position management: Take profit at the 24-bar midpoint recovery or time-exit
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            target_midpoint = (self.flush_high + self.flush_low) / 2.0 if self.flush_high > self.flush_low else 0.0
            
            # Reached flush midpoint or held for max 8 bars
            if (target_midpoint > 0 and current_close >= target_midpoint) or bars_in_trade >= 8:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "target_midpoint_hit" if current_close >= target_midpoint else "time_horizon_exit",
                        "current_close": current_close,
                        "target_midpoint": target_midpoint,
                        "bars_in_trade": bars_in_trade,
                    }
                )
            return None

        # Respect post-trade cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check for 24-bar rolling flush
        high_24 = max(highs[-self.lookback:])
        low_24 = min(lows[-self.lookback:])
        past_close = closes[-self.lookback]
        rolling_return = (current_close - past_close) / past_close

        # Oversold indicator check to confirm extreme exhaustion
        rsi = self._rsi(closes, 14)
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Trigger logic:
        # 1. Rolling 24-bar drop exceeds 5%
        # 2. Reversal confirmation: current bar closes above previous bar's high
        # 3. Oversold / sentiment confirmation: RSI < 42 or Extreme Fear/Fear (<45)
        is_flushed = rolling_return <= self.flush_threshold_pct
        bullish_engulf_prev_high = current_close > prev_high
        is_depressed = (rsi is not None and rsi < 42.0) or (fear_greed < 45)

        if is_flushed and bullish_engulf_prev_high and is_depressed:
            self.entry_bar = ctx.bar_index
            self.flush_high = high_24
            self.flush_low = low_24
            confidence = min(1.0, max(0.55, abs(rolling_return) * 8.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flush_recovery_reversal_bar",
                    "rolling_24h_return": rolling_return,
                    "rsi": rsi if rsi is not None else 0.0,
                    "fear_greed": fear_greed,
                    "prev_high": prev_high,
                    "close": current_close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index