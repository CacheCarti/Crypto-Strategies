from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBTC(Strategy):
    METADATA = {
        "name": "CrashRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold_pct = 0.050  # 5.0% drop over 24 bars for reliable setup frequency
        self.max_hold_bars = 10
        self.cooldown_period = 12
        self.entry_bar_idx = -1
        self.last_exit_bar_idx = -999
        self.midpoint_target = 0.0

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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = closes[-1]
        prev_high = highs[-2]
        
        # Position management
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar_idx
            
            # Midpoint take-profit condition
            if self.midpoint_target > 0 and current_close >= self.midpoint_target:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "price": current_close,
                        "target": self.midpoint_target,
                        "bars_in_trade": bars_in_trade
                    }
                )

            # Max time-based hold exit
            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "flush_recovery_time_horizon_reached",
                        "price": current_close,
                        "bars_in_trade": bars_in_trade
                    }
                )
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar_idx < self.cooldown_period:
            return None

        # Evaluate 24-bar drop
        rolling_24_high = max(highs[-self.lookback:])
        rolling_24_low = min(lows[-self.lookback:])
        
        if rolling_24_high <= 0:
            return None
            
        drop_pct = (rolling_24_high - current_close) / rolling_24_high

        # Crash setup: flush of at least drop_threshold_pct
        if drop_pct >= self.drop_threshold_pct:
            # Reversal confirmation: close above previous bar's high
            if current_close > prev_high:
                rsi_val = self._rsi(closes, period=14)
                # Confirm we are rebounding from an oversold/depressed momentum state
                if rsi_val is not None and rsi_val < 50.0:
                    self.entry_bar_idx = ctx.bar_index
                    self.midpoint_target = (rolling_24_high + rolling_24_low) / 2.0
                    
                    # Confidence scaling based on severity of the flush
                    severity_bonus = min(0.3, max(0.0, (drop_pct - self.drop_threshold_pct) * 5.0))
                    confidence = min(0.95, 0.65 + severity_bonus)
                    
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "crash_flush_capitulation_reversal",
                            "drop_pct": round(drop_pct * 100, 2),
                            "rolling_high": rolling_24_high,
                            "rolling_low": rolling_24_low,
                            "midpoint_target": self.midpoint_target,
                            "rsi": round(rsi_val, 2),
                            "price": current_close
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar_idx = ctx.bar_index
        self.entry_bar_idx = -1
        self.midpoint_target = 0.0