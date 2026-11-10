from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryRebound(Strategy):
    METADATA = {
        "name": "CrashRecoveryRebound",
        "domain": "btc_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,  # 12 hours intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16
        self.entry_midpoint = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_idx = ctx.bar_index
        current_close = ctx.bar.close

        # Position Management
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            
            # Midpoint mean-reversion exit
            if self.entry_midpoint > 0 and current_close >= self.entry_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "midpoint_recovery_reached",
                        "price": current_close,
                        "midpoint": self.entry_midpoint,
                        "bars_held": bars_held,
                    }
                )

            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_expired",
                        "price": current_close,
                        "bars_held": bars_held,
                    }
                )
            return None

        # Entry Gate: Cooldown to avoid overtrading churn
        if current_idx - self.last_exit_bar < self.cooldown_period:
            return None

        # Calculate dynamic crash threshold using recent range and ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        
        # Drawdown across the last 24 bars
        drop_pct = (window_high - current_close) / window_high
        
        # Dynamic drop threshold: at least 3.5x normalized ATR or minimum 4.5% drop
        atr_drop_pct = (atr * 3.5) / current_close
        crash_threshold = max(0.045, atr_drop_pct)

        # Trigger condition: Flush followed by breakout above previous bar high
        prev_bar_high = highs[-2]
        is_capitulation_breakout = (current_close > prev_bar_high) and (closes[-2] <= prev_bar_high)
        is_flushed = drop_pct >= crash_threshold

        # Sentiment gate: Extreme panic/fear environments offer asymmetric rebound edge
        fear_greed = ctx.features.get("fear_greed_index", 50)
        sentiment_ok = fear_greed <= 40

        if is_flushed and is_capitulation_breakout and sentiment_ok:
            self.entry_bar = current_idx
            self.entry_midpoint = window_low + (window_high - window_low) * 0.5

            # Dynamic confidence based on depth of crash
            confidence = min(0.9, max(0.6, drop_pct / 0.10))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "crash_flush_reversal_breakout",
                    "drop_pct": round(drop_pct * 100, 2),
                    "crash_threshold_pct": round(crash_threshold * 100, 2),
                    "fear_greed": fear_greed,
                    "target_midpoint": round(self.entry_midpoint, 2),
                    "price": current_close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_midpoint = 0.0