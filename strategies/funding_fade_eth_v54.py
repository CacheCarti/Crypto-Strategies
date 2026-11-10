from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_hist = []
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.max_hold_bars = 24

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values, period):
        if len(values) < period:
            return values[-1] if values else 0.0
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_hist.append(current_funding)
        if len(self.funding_hist) > 50:
            self.funding_hist.pop(0)

        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"] or len(self.funding_hist) < 4:
            return None

        # Check market context to avoid trading during absolute crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.8:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        # Persistent funding check over the last 3 bars
        f_window = self.funding_hist[-3:]
        avg_funding = sum(f_window) / len(f_window)
        rsi = self._rsi(closes, 14)
        ema30 = self._ema(closes, 30)
        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction") if pos else None
            # Normalize funding exit: exit when crowd positioning is back to neutral
            if direction == "long" and avg_funding >= 0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_long_exit", "funding": avg_funding, "rsi": rsi, "price": price}
                )
            elif direction == "short" and avg_funding <= -0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_short_exit", "funding": avg_funding, "rsi": rsi, "price": price}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Price filters: ensure we don't fade into severe trend breakouts
        dist_ema_bps = abs(price - ema30) / ema30 * 10000.0
        if dist_ema_bps > 350.0:
            return None

        # Long entry setup: persistent negative funding (shorts paying) + RSI not overbought
        # Funding rate threshold 0.00005 per 8h is ~0.005%
        if all(f <= -0.00005 for f in f_window) and rsi < 58.0:
            conf = min(0.9, 0.55 + abs(avg_funding) * 2000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_negative_funding_squeeze",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "price": price,
                    "dist_ema_bps": dist_ema_bps
                }
            )

        # Short entry setup: persistent positive funding (longs paying) + RSI not oversold
        if all(f >= 0.00010 for f in f_window) and rsi > 42.0:
            conf = min(0.9, 0.55 + avg_funding * 2000.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_positive_funding_reversion",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "price": price,
                    "dist_ema_bps": dist_ema_bps
                }
            )

        return None