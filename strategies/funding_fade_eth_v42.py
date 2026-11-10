from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_threshold_high = 0.00018  # Crowd over-leveraged long (0.018% / 8h)
        self.funding_threshold_low = -0.00008  # Crowd paying to short (-0.008% / 8h)
        self.cooldown_bars = 6                 # Avoid immediate re-entry after close
        self.last_exit_bar = -999
        self.bars_in_position = 0
        self.funding_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Average funding over last 3 bars to require persistence and filter single-bar noise
        recent_funding = sum(self.funding_history[-3:]) / len(self.funding_history[-3:])

        ema_trend = self._ema(closes, 30)
        if ema_trend is None:
            return None

        current_close = ctx.bar.close
        rsi = self._rsi(closes, 14)
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle active position management and dynamic exits
        if has_pos:
            self.bars_in_position += 1
            # Normalization exit: crowd positioning has reset, edge dissipated
            if pos_dir == "long" and recent_funding >= 0.00005:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_long_exit", "funding": current_funding, "rsi": rsi}
                )
            if pos_dir == "short" and recent_funding <= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_short_exit", "funding": current_funding, "rsi": rsi}
                )
            # Max hold time guard to prevent capital freeze
            if self.bars_in_position >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exit", "bars_held": self.bars_in_position}
                )
            return None

        # Post-exit cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Do not fade into systemic breakdown/crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Entry Conditions:
        # Long: Crowd aggressively short (funding < low threshold), but price isn't in freefall collapse (rsi > 30 and price near EMA)
        long_setup = (
            recent_funding < self.funding_threshold_low
            and current_close > ema_trend * 0.985
            and rsi >= 32.0
            and rsi <= 60.0
        )

        # Short: Crowd aggressively long (funding > high threshold), price not in runaway parabolic melt-up (rsi < 70)
        short_setup = (
            recent_funding > self.funding_threshold_high
            and current_close < ema_trend * 1.015
            and rsi <= 68.0
            and rsi >= 40.0
        )

        if long_setup:
            self.bars_in_position = 0
            conf = min(0.85, 0.65 + abs(recent_funding) * 500)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_negative_squeeze_long",
                    "funding": current_funding,
                    "avg_funding": recent_funding,
                    "rsi": round(rsi, 2),
                    "price_to_ema": round(current_close / ema_trend, 4),
                }
            )

        if short_setup:
            self.bars_in_position = 0
            conf = min(0.85, 0.65 + abs(recent_funding) * 500)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_positive_overheated_short",
                    "funding": current_funding,
                    "avg_funding": recent_funding,
                    "rsi": round(rsi, 2),
                    "price_to_ema": round(current_close / ema_trend, 4),
                }
            )

        return None