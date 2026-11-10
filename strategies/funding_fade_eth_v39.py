from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateReversion(Strategy):
    METADATA = {
        "name": "Funding Rate Contrarian Squeeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.funding_history = []
        self.persistence_needed = 3
        # 0.01% standard per 8h ~ 0.0001. Thresholds: +-0.015% (0.00015)
        self.extreme_pos_threshold = 0.00015
        self.extreme_neg_threshold = -0.00015
        self.norm_threshold = 0.00005

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
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track funding rate persistence
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        ema30 = self._ema(closes, 30)
        rsi14 = self._rsi(closes, 14)
        if ema30 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        # Check existing position for exit conditions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Normalize exit: funding returned to near-zero neutral
            if abs(funding) < self.norm_threshold:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_normalized",
                        "funding": funding,
                        "price": current_price,
                        "rsi": round(rsi14, 2),
                    },
                )
            # RSI overextension counter-reversal exit
            if pos_dir == "long" and rsi14 > 72.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_take_profit_long", "rsi": round(rsi14, 2), "funding": funding},
                )
            if pos_dir == "short" and rsi14 < 28.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_take_profit_short", "rsi": round(rsi14, 2), "funding": funding},
                )
            return None

        # Mandatory cooldown after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if len(self.funding_history) < self.persistence_needed:
            return None

        recent_funding = self.funding_history[-self.persistence_needed:]
        persist_neg = all(f <= self.extreme_neg_threshold for f in recent_funding)
        persist_pos = all(f >= self.extreme_pos_threshold for f in recent_funding)

        # Light price filters: avoid fading hyper-extended runaways
        # For long squeeze: crowd heavily short, but price not in a complete freefall (RSI > 25) and not overbought (RSI < 65)
        # Price should be stabilizing near or above EMA30 - 2.5% band
        ema_ratio = (current_price - ema30) / ema30

        if persist_neg and 25.0 <= rsi14 <= 60.0 and ema_ratio > -0.035:
            conf = min(0.9, 0.6 + abs(funding) * 500)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_negative_funding_squeeze",
                    "funding": funding,
                    "avg_recent_funding": sum(recent_funding) / len(recent_funding),
                    "rsi": round(rsi14, 2),
                    "price": current_price,
                    "ema30": round(ema30, 2),
                },
            )

        # For short squeeze fade: crowd heavily long, but price not breakout runaway (RSI < 75) and not oversold (RSI > 40)
        if persist_pos and 40.0 <= rsi14 <= 75.0 and ema_ratio < 0.035:
            conf = min(0.9, 0.6 + abs(funding) * 500)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_positive_funding_fade",
                    "funding": funding,
                    "avg_recent_funding": sum(recent_funding) / len(recent_funding),
                    "rsi": round(rsi14, 2),
                    "price": current_price,
                    "ema30": round(ema30, 2),
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index