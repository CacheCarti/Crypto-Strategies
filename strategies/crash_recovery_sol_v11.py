from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_flush = 24
        self.flush_drop_threshold = 0.065  # 6.5% drop over 24h
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.panic_low = None
        self.panic_high = None

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
        self.panic_low = None
        self.panic_high = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = max(self.lookback_flush + 2, self.METADATA["warmup_bars"])
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)

        # In-position management
        if ctx.has_position():
            # Check if midpoint target reached
            if self.panic_high is not None and self.panic_low is not None:
                midpoint = (self.panic_high + self.panic_low) / 2.0
                if ctx.bar.close >= midpoint:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_midpoint_target_reached",
                            "close": ctx.bar.close,
                            "midpoint": midpoint,
                            "panic_high": self.panic_high,
                            "panic_low": self.panic_low
                        }
                    )
            return None

        # Cooldown guard to avoid churn after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush calculation over lookback
        recent_high = max(highs[-self.lookback_flush:])
        recent_low = min(lows[-self.lookback_flush:])
        prev_close = closes[-2]
        curr_close = closes[-1]
        prev_high = highs[-2]

        drop_from_high = (recent_high - recent_low) / recent_high

        # 1. Panic drop condition: flush must be >= 6.5%
        if drop_from_high < self.flush_drop_threshold:
            return None

        # 2. Rebound stabilization: current bar closes above previous bar's high
        # and current close is an upward green candle
        rebound_confirmed = (curr_close > prev_high) and (curr_close > ctx.bar.open)
        if not rebound_confirmed:
            return None

        # 3. Oversold / sentiment context: avoid catching knives in unending bleed
        rsi_val = self._rsi(closes, period=14)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Must show panic condition (RSI low or Fear & Greed depressed)
        if rsi_val > 45.0 and fg_index > 45.0:
            return None

        # Dynamic Stop Loss under the panic low
        dist_to_low = curr_close - recent_low
        if dist_to_low <= 0:
            sl_bps = 250.0
        else:
            sl_bps = min(max((dist_to_low / curr_close) * 10000.0 + 30.0, 200.0), 500.0)

        # Target midpoint recovery
        midpoint = (recent_high + recent_low) / 2.0
        tp_dist = midpoint - curr_close
        if tp_dist > 0:
            tp_bps = min(max((tp_dist / curr_close) * 10000.0, 250.0), 650.0)
        else:
            tp_bps = 350.0

        # Save context for position handling
        self.panic_high = recent_high
        self.panic_low = recent_low

        confidence = 0.75 if drop_from_high > 0.09 else 0.65

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=21600,
            metadata={
                "reason": "sol_panic_flush_rebound",
                "drop_pct": drop_from_high * 100.0,
                "rsi": rsi_val,
                "fear_greed": fg_index,
                "panic_high": recent_high,
                "panic_low": recent_low,
                "sl_bps": sl_bps,
                "tp_bps": tp_bps
            }
        )