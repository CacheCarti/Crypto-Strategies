from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingTiltedRsiScalp(Strategy):
    METADATA = {
        "name": "FundingTiltedRsiScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 190.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 7
        self.vol_period = 20
        self.oversold_thresh = 20.0
        self.overbought_thresh = 80.0
        self.long_funding_thresh = -0.00003   # Clearly negative funding (shorts paying)
        self.short_funding_thresh = 0.00012   # Elevated positive funding (longs paying heavily)
        self.cooldown_bars = 40               # ~3.3 hours cooldown to prevent overtrading
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period: int = 7) -> Optional[float]:
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

        # Filter out extreme market crisis
        if ctx.regime == "crisis":
            return None

        closes = ctx.closes(self.rsi_period + 10)
        volumes = ctx.volumes(self.vol_period + 5)
        if len(closes) < self.rsi_period + 1 or len(volumes) < self.vol_period:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        avg_vol = sum(volumes[-self.vol_period:]) / self.vol_period
        current_vol = ctx.bar.volume
        vol_surge = current_vol >= (1.15 * avg_vol) if avg_vol > 0 else True

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_price = ctx.bar.close

        # Position management / exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Fast scalp exit on mean reversion or time stop
            if pos_dir == "long" and (rsi >= 52.0 or bars_held >= 10):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_normalized_long" if rsi >= 52.0 else "max_hold_scalp_long",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                        "bars_held": bars_held,
                    },
                )
            elif pos_dir == "short" and (rsi <= 48.0 or bars_held >= 10):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_normalized_short" if rsi <= 48.0 else "max_hold_scalp_short",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Hard cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long Entry: Negative funding bias + extreme RSI dip + bullish reversal candle + volume confirmation
        bullish_candle = ctx.bar.close > ctx.bar.open
        if funding < self.long_funding_thresh and rsi <= self.oversold_thresh and bullish_candle and vol_surge:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "negative_funding_extreme_oversold_bounce",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "price": current_price,
                    "volume_ratio": round(current_vol / max(avg_vol, 1e-6), 2),
                },
            )

        # Short Entry: Elevated positive funding + extreme RSI spike + bearish reversal candle + volume confirmation
        bearish_candle = ctx.bar.close < ctx.bar.open
        if funding > self.short_funding_thresh and rsi >= self.overbought_thresh and bearish_candle and vol_surge:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "positive_funding_extreme_overbought_reversal",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "price": current_price,
                    "volume_ratio": round(current_vol / max(avg_vol, 1e-6), 2),
                },
            )

        return None