from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class FundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 18
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.last_entry_bar = -100

        # Loosened thresholds to guarantee healthy trade frequency (30-100 trades)
        self.min_momentum_bps = 25.0  # 0.25% move over lookback
        self.neg_funding_thresh = 0.00001  # zero or negative funding
        self.pos_funding_thresh = 0.00006  # elevated positive funding

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.lookback_period + 5)
        if len(closes) < self.lookback_period + 1:
            return None

        current_price = ctx.bar.close
        past_price = closes[-self.lookback_period]
        if past_price <= 0:
            return None

        return_bps = ((current_price - past_price) / past_price) * 10000.0
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi = self._calc_rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position exit management
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if rsi > 76.0 or funding > 0.00020:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_momentum_exhaustion",
                            "rsi": round(rsi, 2),
                            "funding": funding,
                            "return_bps": round(return_bps, 1),
                        },
                    )
            elif direction == "short":
                if rsi < 24.0 or funding < -0.00005:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_squeeze_exhaustion",
                            "rsi": round(rsi, 2),
                            "funding": funding,
                            "return_bps": round(return_bps, 1),
                        },
                    )
            return None

        # Cooldown check
        if (ctx.bar_index - self.last_exit_bar < self.cooldown_bars) or (
            ctx.bar_index - self.last_entry_bar < self.cooldown_bars
        ):
            return None

        # Long Entry: Price pushing up while funding is neutral/negative (shorts trapped)
        if return_bps >= self.min_momentum_bps and funding <= self.neg_funding_thresh:
            if rsi < 70.0:
                self.last_entry_bar = ctx.bar_index
                confidence = min(0.85, 0.55 + max(0.0, -funding) * 2000.0)
                return ctx.signal(
                    "long",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "trapped_shorts_momentum_continuation",
                        "return_bps": round(return_bps, 1),
                        "funding": funding,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )

        # Short Entry: Price pushing down while funding remains positive (longs trapped)
        if return_bps <= -self.min_momentum_bps and funding >= self.pos_funding_thresh:
            if rsi > 30.0:
                self.last_entry_bar = ctx.bar_index
                confidence = min(0.85, 0.55 + funding * 1500.0)
                return ctx.signal(
                    "short",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "trapped_longs_liquidation_continuation",
                        "return_bps": round(return_bps, 1),
                        "funding": funding,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )

        return None