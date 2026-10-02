from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.drop_threshold = 0.045  # 4.5% flush threshold
        self.rsi_period = 14
        self.rsi_max_entry = 48.0
        self.cooldown_bars = 3
        self.last_exit_bar = -999
        self.entry_flush_mid = 0.0

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
        self.entry_flush_mid = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 5:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position Management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if self.entry_flush_mid > 0 and current_close >= self.entry_flush_mid:
                    return ctx.signal(
                        "flat",
                        confidence=0.80,
                        metadata={
                            "reason": "flush_midpoint_target_reached",
                            "current_close": current_close,
                            "midpoint": round(self.entry_flush_mid, 2),
                            "rsi": round(rsi, 2),
                        },
                    )
                if rsi >= 66.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "rsi_overbought_exit",
                            "current_close": current_close,
                            "rsi": round(rsi, 2),
                        },
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        peak_high = max(recent_highs)
        trough_low = min(recent_lows)

        if peak_high <= 0:
            return None

        # Check panic drop over recent lookback
        flush_depth = (peak_high - trough_low) / peak_high
        drop_from_peak = (current_close - peak_high) / peak_high

        # Stabilization: current green bar closing above previous bar's open or high
        prev_high = highs[-2]
        prev_open = ctx.opens(3)[-2]
        is_green_bar = current_close > current_open
        stabilized = is_green_bar and (current_close > prev_high or current_close > prev_open)

        # Entry triggered on panic drop + stabilization + reasonable RSI
        if flush_depth >= self.drop_threshold and drop_from_peak <= -0.03 and rsi <= self.rsi_max_entry and stabilized:
            midpoint = trough_low + ((peak_high - trough_low) * 0.5)

            sl_dist = max(current_close - trough_low, current_close * 0.018)
            sl_bps = min(max((sl_dist / current_close) * 10000.0 + 20.0, 180.0), 380.0)

            tp_dist = max(midpoint - current_close, current_close * 0.025)
            tp_bps = min(max((tp_dist / current_close) * 10000.0, 220.0), 480.0)

            self.entry_flush_mid = midpoint

            depth_ratio = min(flush_depth / 0.10, 1.0)
            confidence = round(0.65 + 0.25 * depth_ratio, 2)
            fg_index = ctx.features.get("fear_greed_index", 50)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=14400,
                metadata={
                    "reason": "sol_panic_recovery_bounce",
                    "flush_depth_pct": round(flush_depth * 100.0, 2),
                    "drop_from_peak_pct": round(drop_from_peak * 100.0, 2),
                    "peak_high": round(peak_high, 2),
                    "trough_low": round(trough_low, 2),
                    "midpoint": round(midpoint, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed_index": fg_index,
                },
            )

        return None