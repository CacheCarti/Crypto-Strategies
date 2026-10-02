from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 20
        self.drop_threshold = 0.035  # 3.5% peak-to-trough drop
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.target_price = 0.0
        self.panic_low = 0.0

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.panic_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback_bars + 15)
        highs = ctx.highs(self.lookback_bars + 15)
        lows = ctx.lows(self.lookback_bars + 15)

        if len(closes) < self.lookback_bars + 2:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        prev_close = closes[-2]

        rsi = self._rsi(closes, period=14)
        rsi_val = rsi if rsi is not None else 50.0

        # Position Management
        if ctx.has_position():
            if self.target_price > 0.0 and current_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "current_price": current_close,
                        "target_price": self.target_price,
                        "rsi": round(rsi_val, 2),
                    },
                )
            if rsi_val >= 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "rsi_overbought_exit",
                        "current_price": current_close,
                        "rsi": round(rsi_val, 2),
                    },
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate flush drop magnitude over recent window
        window_highs = highs[-self.lookback_bars:]
        window_lows = lows[-self.lookback_bars:]

        peak_price = max(window_highs)
        trough_price = min(window_lows)

        if peak_price <= 0:
            return None

        drop_magnitude = (peak_price - trough_price) / peak_price

        # Stabilization trigger: bullish candle closing above previous close
        stabilization = (current_close > current_open) and (current_close > prev_close)

        # Ensure we are bouncing from the lower half of the flush range
        midpoint = (peak_price + trough_price) / 2.0
        in_value_zone = current_close < midpoint * 1.01

        if drop_magnitude >= self.drop_threshold and stabilization and in_value_zone:
            self.target_price = midpoint
            self.panic_low = trough_price

            dist_to_low = max(0.005, (current_close - trough_price) / current_close)
            dist_to_mid = max(0.008, (midpoint - current_close) / current_close)

            sl_bps = max(180.0, min(450.0, dist_to_low * 10000.0 + 30.0))
            tp_bps = max(250.0, min(650.0, dist_to_mid * 10000.0))

            confidence = min(0.85, max(0.55, 0.50 + (drop_magnitude * 4.0)))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_stabilization_bounce",
                    "drop_pct": round(drop_magnitude * 100.0, 2),
                    "peak_price": round(peak_price, 2),
                    "trough_price": round(trough_price, 2),
                    "target_midpoint": round(midpoint, 2),
                    "rsi": round(rsi_val, 2),
                    "close": current_close,
                },
            )

        return None