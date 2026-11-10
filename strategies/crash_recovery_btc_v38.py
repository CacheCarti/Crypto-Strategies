from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecoveryBtc(Strategy):
    METADATA = {
        "name": "PanicRecoveryBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        
        if len(closes) < self.lookback + 1:
            return None

        # Manage existing position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            window_high = max(highs[-self.lookback:])
            window_low = min(lows[-self.lookback:])
            midpoint = (window_high + window_low) / 2.0
            
            # Midpoint take profit target
            if closes[-1] >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_24bar_midpoint", "midpoint": midpoint, "close": closes[-1]}
                )
            
            # Time-based stop (max hold reached)
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_hold_expired", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market stress filter: Fear & Greed below 45 to ensure genuine capitulation/fear context
        fg_index = ctx.features.get("fear_greed_index", 50)
        if fg_index > 45:
            return None

        # Flush condition: drop >= 5.5% from highest high in the last 24 bars
        recent_high = max(highs[-self.lookback - 1:-1])
        recent_low = min(lows[-self.lookback - 1:-1])
        drop_pct = (recent_high - closes[-2]) / recent_high

        # 5.5% drop qualifies as a panic flush
        if drop_pct < 0.055:
            return None

        # Capitulation reversal candle: close back above previous bar's high
        prev_high = highs[-2]
        curr_close = closes[-1]
        if curr_close <= prev_high:
            return None

        # Confluence: RSI deeply discounted (< 36) to prevent buying bull-market shallow dips
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 36.0:
            return None

        self.entry_bar = ctx.bar_index
        midpoint = (recent_high + recent_low) / 2.0
        confidence = min(0.9, 0.6 + (drop_pct * 3.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=350.0,
            take_profit_bps=450.0,
            horizon_seconds=28800,
            metadata={
                "reason": "flush_reversal_above_prev_high",
                "drop_pct": round(drop_pct, 4),
                "rsi": round(rsi_val, 2),
                "fear_greed": fg_index,
                "midpoint_target": round(midpoint, 2),
                "prev_high": prev_high,
                "curr_close": curr_close
            }
        )