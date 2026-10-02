from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_trade_bar = -999
        self.price_thresh = 0.012
        self.funding_neg_thresh = -0.00003
        self.funding_pos_thresh = 0.00008

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        ret_lookback = (current_close - past_close) / past_close

        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        bars_since_trade = ctx.bar_index - self.last_trade_bar
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic for open positions
        if has_pos:
            if pos_dir == "long":
                if rsi > 78.0 or funding_rate > 0.00025:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_squeeze_exhaustion",
                            "rsi": round(rsi, 2),
                            "funding_rate": funding_rate,
                            "ret_lookback": round(ret_lookback, 4),
                        },
                    )
            elif pos_dir == "short":
                if rsi < 22.0 or funding_rate < -0.00015:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_squeeze_exhaustion",
                            "rsi": round(rsi, 2),
                            "funding_rate": funding_rate,
                            "ret_lookback": round(ret_lookback, 4),
                        },
                    )

        # Mandatory cooldown check before opening new positions
        if bars_since_trade < self.cooldown_bars or has_pos:
            return None

        # Short squeeze continuation: Price rising, shorts paying funding (negative funding)
        if ret_lookback > self.price_thresh and funding_rate <= self.funding_neg_thresh:
            if 42.0 <= rsi <= 68.0:
                self.last_trade_bar = ctx.bar_index
                funding_intensity = min(abs(funding_rate) / 0.0002, 1.0)
                confidence = round(0.60 + 0.30 * funding_intensity, 2)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "negative_funding_short_squeeze_breakout",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding_rate,
                        "ret_lookback": round(ret_lookback, 4),
                        "close_price": current_close,
                    },
                )

        # Long liquidation cascade: Price falling, longs paying elevated funding
        if ret_lookback < -self.price_thresh and funding_rate >= self.funding_pos_thresh:
            if 32.0 <= rsi <= 58.0:
                self.last_trade_bar = ctx.bar_index
                funding_intensity = min(funding_rate / 0.0003, 1.0)
                confidence = round(0.60 + 0.30 * funding_intensity, 2)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "positive_funding_long_squeeze_breakdown",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding_rate,
                        "ret_lookback": round(ret_lookback, 4),
                        "close_price": current_close,
                    },
                )

        return None