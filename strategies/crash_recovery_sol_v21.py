from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SOL Panic Flush Recovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.07
        self.last_exit_bar = -999
        self.cooldown_bars = 10
        self.entry_bar = -999
        self.panic_low = 0.0
        self.panic_high = 0.0

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        if ctx.has_position():
            target_midpoint = (self.panic_high + self.panic_low) / 2.0
            bars_held = ctx.bar_index - self.entry_bar

            if current_close >= target_midpoint and target_midpoint > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_range_midpoint_target_reached",
                        "price": current_close,
                        "target_midpoint": round(target_midpoint, 2),
                        "bars_held": bars_held,
                    },
                )

            if current_close < self.panic_low and self.panic_low > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "stop_under_panic_low_breached",
                        "price": current_close,
                        "panic_low": round(self.panic_low, 2),
                        "bars_held": bars_held,
                    },
                )

            rsi_val = self._rsi(closes, period=14)
            if rsi_val is not None and rsi_val > 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_rebound_overbought",
                        "rsi": round(rsi_val, 1),
                        "price": current_close,
                        "bars_held": bars_held,
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        drop_pct = (recent_high - current_close) / recent_high

        if drop_pct < self.drop_threshold:
            return None

        prev_bar_high = highs[-2]
        prev_bar_close = closes[-2]
        is_stabilization = (current_close > prev_bar_high) and (current_close > prev_bar_close)
        if not is_stabilization:
            return None

        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 45.0:
            return None

        fgi = ctx.features.get("fear_greed_index", 50.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        self.panic_low = recent_low
        self.panic_high = recent_high
        self.entry_bar = ctx.bar_index

        stop_dist_bps = max(180.0, min(500.0, ((current_close - recent_low) / current_close) * 10000.0 + 30.0))
        midpoint = (recent_high + recent_low) / 2.0
        tp_dist_bps = max(250.0, min(650.0, ((midpoint - current_close) / current_close) * 10000.0))

        confidence = 0.70
        if drop_pct >= 0.10:
            confidence += 0.15
        if fgi < 30.0:
            confidence += 0.10
        confidence = min(1.0, confidence)

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=round(stop_dist_bps, 1),
            take_profit_bps=round(tp_dist_bps, 1),
            horizon_seconds=21600,
            metadata={
                "reason": "sol_flush_stabilization_rebound",
                "drop_pct": round(drop_pct * 100, 2),
                "rsi": round(rsi_val, 1),
                "panic_low": round(recent_low, 2),
                "panic_high": round(recent_high, 2),
                "fgi": fgi,
                "crisis_score": crisis_score,
                "price": current_close,
            },
        )