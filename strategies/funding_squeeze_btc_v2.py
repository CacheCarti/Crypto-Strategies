from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingRateSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.roc_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.min_roc_pct = 0.60
        self.funding_short_trap = 0.00000
        self.funding_long_trap = 0.00012

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.roc_period + self.rsi_period + 5)
        if len(closes) < self.roc_period + 1:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        current_close = closes[-1]
        past_close = closes[-self.roc_period]
        price_roc = ((current_close - past_close) / past_close) * 100.0
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.last_entry_bar

            # Take profit on momentum exhaustion
            if pos_dir == "long" and (rsi_val > 78.0 or price_roc < -0.5):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_momentum_exhaustion",
                        "rsi": round(rsi_val, 2),
                        "price_roc": round(price_roc, 3),
                        "funding": funding,
                        "bars_held": bars_in_trade,
                    },
                )
            elif pos_dir == "short" and (rsi_val < 22.0 or price_roc > 0.5):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_momentum_exhaustion",
                        "rsi": round(rsi_val, 2),
                        "price_roc": round(price_roc, 3),
                        "funding": funding,
                        "bars_held": bars_in_trade,
                    },
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Long Entry: Price rising but funding is zero/negative -> Shorts trapped in squeeze
        if (
            price_roc >= self.min_roc_pct
            and funding <= self.funding_short_trap
            and 45.0 <= rsi_val <= 72.0
        ):
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + (price_roc / 5.0) + abs(min(0.0, funding) * 1000.0))
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=240.0,
                take_profit_bps=480.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_roc_20": round(price_roc, 3),
                    "funding_rate": funding,
                    "rsi": round(rsi_val, 2),
                    "close": current_close,
                },
            )

        # Short Entry: Price falling but funding is elevated -> Longs trapped in liquidation cascade
        if (
            price_roc <= -self.min_roc_pct
            and funding >= self.funding_long_trap
            and 28.0 <= rsi_val <= 55.0
        ):
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + (abs(price_roc) / 5.0) + (funding * 1000.0))
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=240.0,
                take_profit_bps=480.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "long_liquidation_squeeze",
                    "price_roc_20": round(price_roc, 3),
                    "funding_rate": funding,
                    "rsi": round(rsi_val, 2),
                    "close": current_close,
                },
            )

        return None