from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_drop = 24
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.entry_price = 0.0
        self.target_price = 0.0
        self.flush_high = 0.0
        self.flush_low = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[-i] - closes[-i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_price = 0.0
        self.target_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_drop + 5)
        highs = ctx.highs(self.lookback_drop + 5)
        lows = ctx.lows(self.lookback_drop + 5)

        if len(closes) < self.lookback_drop + 2:
            return None

        # Check existing position for take-profit at recovery midpoint
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if self.target_price > 0 and ctx.bar.close >= self.target_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_midpoint_target_reached",
                            "close": ctx.bar.close,
                            "target": self.target_price,
                        }
                    )
            return None

        # Post-exit cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries during absolute structural crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        recent_high = max(highs[-self.lookback_drop:])
        current_close = ctx.bar.close
        prev_bar_high = highs[-2]

        drop_pct = (recent_high - current_close) / recent_high
        rsi_val = self._rsi(closes, period=14)

        # Trigger conditions:
        # 1. 24-bar drop >= 4.5% (elastic panic threshold)
        # 2. RSI deeply depressed (< 38)
        # 3. Stabilization confirmation: current close > previous bar's high
        if drop_pct >= 0.045 and rsi_val < 38.0 and current_close > prev_bar_high:
            self.flush_high = recent_high
            self.flush_low = min(lows[-self.lookback_drop:])
            # Midpoint of the flush drop range
            self.target_price = self.flush_low + 0.50 * (self.flush_high - self.flush_low)
            self.entry_price = current_close

            # Calculate adaptive dynamic take-profit in bps towards target
            dist_bps = ((self.target_price - current_close) / current_close) * 10000.0
            tp_bps = max(150.0, min(500.0, dist_bps))

            confidence = 0.65
            fear_greed = ctx.features.get("fear_greed_index", 50.0)
            if fear_greed < 30:
                confidence += 0.15

            return ctx.signal(
                "long",
                confidence=min(1.0, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_stabilization_rebound",
                    "drop_pct": round(drop_pct * 100.0, 2),
                    "rsi": round(rsi_val, 2),
                    "target_midpoint": round(self.target_price, 2),
                    "fear_greed": fear_greed,
                }
            )

        return None