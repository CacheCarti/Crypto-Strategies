from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.045
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0

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
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        if ctx.has_position():
            # Exit near the flush midpoint or after holding for 18 hours max
            bars_held = current_bar - self.entry_bar
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "target_midpoint_rebound_reached",
                        "target_midpoint": self.target_midpoint,
                        "close": current_close,
                        "bars_held": bars_held,
                    },
                )
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_time_reached",
                        "close": current_close,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Mandatory cooldown check
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis filter: prevent entering during ongoing system meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Evaluate drop over past 24 bars
        high_24 = max(highs[-self.lookback - 1 : -1])
        low_24 = min(lows[-self.lookback - 1 : -1])

        if high_24 <= 0.0:
            return None

        drop_pct = (high_24 - current_close) / high_24

        # Drop must exceed flush threshold (4.5% drop in 24h)
        if drop_pct >= self.drop_threshold:
            prev_high = highs[-2]
            # Stabilization trigger: current close breaks above previous bar high
            if current_close > prev_high:
                # Confirm washed out momentum with RSI
                rsi_val = self._rsi(closes, period=14)
                if rsi_val is not None and rsi_val < 42.0:
                    midpoint = (high_24 + low_24) / 2.0
                    # Only take trade if midpoint offers at least 100 bps upside
                    if midpoint > current_close * 1.01:
                        self.target_midpoint = midpoint
                        self.entry_bar = current_bar
                        confidence = min(1.0, 0.60 + (drop_pct - self.drop_threshold) * 4.0)

                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "panic_drop_stabilization_rebound",
                                "drop_pct": drop_pct,
                                "rsi": rsi_val,
                                "target_midpoint": self.target_midpoint,
                                "high_24": high_24,
                                "low_24": low_24,
                            },
                        )

        return None