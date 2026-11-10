from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingReversion(Strategy):
    METADATA = {
        "name": "EthFundingReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 120,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.max_funding_window = 240
        self.cooldown_remaining = 0
        self.cooldown_period = 6
        self.rsi_period = 14
        self.trend_ema_period = 50

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_remaining = self.cooldown_period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.max_funding_window:
            self.funding_history.pop(0)

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.trend_ema_period + 15)
        if len(closes) < self.trend_ema_period:
            return None

        # Calculate percentile rank of current funding over rolling history
        sorted_window = sorted(self.funding_history)
        n = len(sorted_window)
        rank = sum(1 for x in sorted_window if x <= current_funding)
        funding_percentile = (rank / n) * 100.0 if n > 0 else 50.0

        # Technical context: price trend & momentum to avoid fading freight trains
        ema50 = self._ema(closes, self.trend_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        if ema50 is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Position management: exit when funding normalizes towards median
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and funding_percentile >= 50.0:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "funding_normalized_long_exit", "pct": funding_percentile, "funding": current_funding}
                )
            elif pos_dir == "short" and funding_percentile <= 50.0:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "funding_normalized_short_exit", "pct": funding_percentile, "funding": current_funding}
                )
            return None

        if self.cooldown_remaining > 0:
            return None

        # Persistence check across recent 3 bars
        if len(self.funding_history) < 3:
            return None
        recent_3 = self.funding_history[-3:]

        # Contrarian Long: Crowd extremely short (funding persistent bottom 10th percentile), not in freefall
        if funding_percentile <= 10.0 and all(f < 0.0 for f in recent_3):
            # Not in deep capitulation (RSI > 32) and not extended far below EMA (within 3.5%)
            if rsi >= 32.0 and current_price >= ema50 * 0.965:
                confidence = min(0.9, 0.55 + (10.0 - funding_percentile) * 0.035)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "contrarian_funding_squeeze_long",
                        "pct": funding_percentile,
                        "funding": current_funding,
                        "rsi": rsi,
                        "ema50": ema50
                    }
                )

        # Contrarian Short: Crowd extremely long (funding persistent top 90th percentile), not vertical bubble
        if funding_percentile >= 90.0 and all(f > 0.00015 for f in recent_3):
            # Not overbought blow-off (RSI < 68) and not extended far above EMA (within 3.5%)
            if rsi <= 68.0 and current_price <= ema50 * 1.035:
                confidence = min(0.9, 0.55 + (funding_percentile - 90.0) * 0.035)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "contrarian_funding_fade_short",
                        "pct": funding_percentile,
                        "funding": current_funding,
                        "rsi": rsi,
                        "ema50": ema50
                    }
                )

        return None