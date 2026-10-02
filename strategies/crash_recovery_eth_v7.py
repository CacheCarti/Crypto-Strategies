from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicFlushRecovery(Strategy):
    METADATA = {
        "name": "Panic Flush Recovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 25,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.min_drop_pct = 0.022
        self.cooldown_period = 3
        self.last_exit_bar = -999
        self.target_midpoint = 0.0
        self.entry_price = 0.0

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
        self.target_midpoint = 0.0
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)
        opens = ctx.opens(self.lookback + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        prev_high = highs[-2]
        rsi_val = self._rsi(closes, 14)

        if ctx.has_position():
            target_reached = self.target_midpoint > 0.0 and current_close >= self.target_midpoint
            rsi_exhausted = rsi_val >= 62.0

            if target_reached or rsi_exhausted:
                self.last_exit_bar = ctx.bar_index
                reason = "midpoint_target_hit" if target_reached else "rsi_exhaustion_exit"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "close": current_close,
                        "target_midpoint": self.target_midpoint,
                        "rsi": rsi_val,
                    },
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Measure recent flush
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        flush_high = max(window_highs)
        flush_low = min(window_lows)

        if flush_high <= 0 or flush_high == flush_low:
            return None

        flush_drop = (flush_high - flush_low) / flush_high
        midpoint = (flush_high + flush_low) / 2.0

        # Relaxed, robust conditions:
        # 1. Lookback experienced a clear drop (>= 2.2%)
        # 2. Stabilization bar: close is above previous high, and is a green candle
        # 3. Entry is still below or near midpoint for favorable reward-to-risk
        # 4. RSI is not severely overbought (< 58)
        is_panic_drop = flush_drop >= self.min_drop_pct
        is_stabilization = (current_close > prev_high) and (current_close >= current_open)
        is_below_midpoint = current_close < (midpoint * 1.005)
        is_rsi_ok = rsi_val < 58.0

        if is_panic_drop and is_stabilization and is_below_midpoint and is_rsi_ok:
            self.target_midpoint = midpoint
            self.entry_price = current_close

            dist_bps = ((midpoint - current_close) / current_close) * 10000.0
            tp_bps = max(min(dist_bps, 400.0), 180.0)
            sl_bps = 250.0

            confidence = min(0.60 + (flush_drop * 3.0), 0.90)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_stabilization_entry",
                    "flush_drop_pct": flush_drop,
                    "flush_high": flush_high,
                    "flush_low": flush_low,
                    "target_midpoint": midpoint,
                    "rsi": rsi_val,
                    "tp_bps": tp_bps,
                },
            )

        return None