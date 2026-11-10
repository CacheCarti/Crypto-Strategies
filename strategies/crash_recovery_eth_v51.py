from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 14
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0
        self.bars_in_trade = 0

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
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 26:
            return None

        # Manage open position
        if ctx.has_position():
            self.bars_in_trade += 1
            curr_price = ctx.bar.close

            # Target reached (mean reversion midpoint) or time-based exhaustion
            if self.target_price > 0.0 and curr_price >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "price": curr_price,
                        "target_price": self.target_price,
                        "bars_held": self.bars_in_trade,
                    },
                )
            if self.bars_in_trade >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "flush_hold_timeout",
                        "price": curr_price,
                        "bars_held": self.bars_in_trade,
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme unmodeled meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # 24-bar drop calculation (looking back from previous bar to measure flush depth)
        recent_high_24 = max(highs[-25:-1])
        recent_low_24 = min(lows[-25:-1])
        drop_pct = (recent_high_24 - recent_low_24) / recent_high_24

        # Real flush requirement: >= 5.5% drop in last 24 bars
        if drop_pct < 0.055:
            return None

        # Stabilization confirmation: current bar must close firmly above previous bar's high
        prev_high = highs[-2]
        curr_close = closes[-1]
        if curr_close <= prev_high:
            return None

        # Deep oversold condition on RSI to avoid entering dead-cat continuations
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi > 36.0:
            return None

        # Calculate midpoint target for the recovery trade
        midpoint = (recent_high_24 + recent_low_24) / 2.0
        # If price already cleared above midpoint, edge is gone
        if curr_close >= midpoint:
            return None

        # Store context for active trade
        self.flush_high = recent_high_24
        self.flush_low = recent_low_24
        self.target_price = midpoint
        self.bars_in_trade = 0

        # Conviction scales with fear sentiment
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        confidence = 0.70
        if fear_greed < 30.0:
            confidence = 0.85

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=280.0,
            take_profit_bps=360.0,
            horizon_seconds=21600,
            metadata={
                "reason": "panic_flush_stabilization_breakout",
                "drop_pct": round(drop_pct * 100, 2),
                "rsi": round(rsi, 2),
                "fear_greed": fear_greed,
                "flush_high": recent_high_24,
                "flush_low": recent_low_24,
                "target_midpoint": round(midpoint, 2),
            },
        )