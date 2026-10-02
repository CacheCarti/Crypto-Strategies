from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingTiltedRsiScalp(Strategy):
    METADATA = {
        "name": "FundingTiltedRsiScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 9
        # Stringent thresholds & meaningful funding imbalances to avoid overtrading
        self.oversold_thresh = 20.0
        self.overbought_thresh = 80.0
        self.funding_short_thresh = 0.00015  # Elevated positive funding (crowded longs)
        self.funding_long_thresh = -0.00002  # Negative funding (crowded shorts)
        self.cooldown_bars = 30  # Hard 2.5 hour cooldown (30 * 5m bars) after exit
        self.last_exit_bar = -999

    def _rsi(self, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        # Filter out extreme crisis regimes to prevent toxic flow slippage
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        closes = ctx.closes(self.rsi_period + 15)
        if len(closes) < self.rsi_period + 2:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        price = ctx.bar.close

        # Position exit management: scale out when RSI mean-reverts
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and rsi >= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "rsi_reverted_to_neutral_long_exit",
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                        "price": price,
                    },
                )
            if direction == "short" and rsi <= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "rsi_reverted_to_neutral_short_exit",
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                        "price": price,
                    },
                )
            return None

        # Hard cooldown check to keep trade frequency within 30-80 trades target
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Candle confirmation: immediate price turn hook
        bullish_hook = closes[-1] > closes[-2]
        bearish_hook = closes[-1] < closes[-2]

        # Long Entry: Negative funding (shorts crowded) + severe RSI oversold dip + turning candle
        if funding <= self.funding_long_thresh and rsi <= self.oversold_thresh and bullish_hook:
            self.last_exit_bar = ctx.bar_index
            confidence = min(0.85, 0.65 + (self.oversold_thresh - rsi) * 0.01)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_negative_extreme_rsi_oversold_hook",
                    "rsi": round(rsi, 2),
                    "funding": round(funding, 6),
                    "price": price,
                },
            )

        # Short Entry: Elevated funding (longs crowded) + severe RSI overbought spike + turning candle
        if funding >= self.funding_short_thresh and rsi >= self.overbought_thresh and bearish_hook:
            self.last_exit_bar = ctx.bar_index
            confidence = min(0.85, 0.65 + (rsi - self.overbought_thresh) * 0.01)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_high_extreme_rsi_overbought_hook",
                    "rsi": round(rsi, 2),
                    "funding": round(funding, 6),
                    "price": price,
                },
            )

        return None