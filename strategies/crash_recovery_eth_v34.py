from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.05
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        prev_high = highs[-2]
        regime = ctx.market.get("regime", "NORMAL")

        # 1. Manage active position: exit when reaching midpoint target or on crisis meltdown
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if regime == "MELTDOWN":
                    return ctx.signal(
                        "flat",
                        confidence=0.9,
                        metadata={"reason": "meltdown_risk_off", "close": current_close}
                    )
                if self.target_price > 0 and current_close >= self.target_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_midpoint_target_reached",
                            "close": current_close,
                            "target": self.target_price
                        }
                    )
            return None

        # 2. Check Cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries during outright systemic market meltdowns
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # 3. Identify 24-bar drop
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])

        if window_high <= 0:
            return None

        # Net drawdown across lookback
        drop_pct = (window_high - window_low) / window_high
        reference_drop = (window_high - current_close) / window_high

        # 4. Stabilization setup:
        # - Net drop at least 5%
        # - Current close above prior bar's high (micro-reversal / stabilization)
        # - RSI check to avoid buying non-exhausted continuation dumps
        rsi_val = self._rsi(closes, 14)
        fear_greed = ctx.features.get("fear_greed_index", 50)

        is_flush = drop_pct >= self.min_drop_pct and reference_drop >= (self.min_drop_pct * 0.7)
        is_stabilized = current_close > prev_high
        rsi_oversold_or_recovering = rsi_val <= 42.0

        if is_flush and is_stabilized and rsi_oversold_or_recovering:
            self.flush_high = window_high
            self.flush_low = window_low
            # Exit near the midpoint of the flush range
            self.target_price = self.flush_low + (self.flush_high - self.flush_low) * 0.50

            # Scale confidence by depth of fear/greed & severity of flush
            confidence = 0.65
            if fear_greed < 30:
                confidence += 0.15
            if drop_pct > 0.08:
                confidence += 0.10
            confidence = min(0.95, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flush_recovery_stabilization_entry",
                    "drop_pct": round(drop_pct * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fear_greed,
                    "target_midpoint": round(self.target_price, 2),
                    "window_high": round(self.flush_high, 2),
                    "window_low": round(self.flush_low, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0