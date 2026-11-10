from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeStrategy(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # 6 hours target hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.rsi_period = 14
        self.min_ret_pct = 0.008  # 0.8% price move over 24h
        self.funding_threshold = 0.00005  # 0.5 bps funding threshold

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Feature extraction
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        curr_price = closes[-1]
        past_price = closes[-self.lookback - 1]
        price_ret = (curr_price - past_price) / past_price

        # Fast momentum indicator to prevent late chase entries
        rsi_vals = ctx.closes(self.rsi_period + 5)
        rsi = self._rsi(rsi_vals, self.rsi_period)
        if rsi is None:
            return None

        # Check regime to avoid crisis / extreme volatility chop
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Long Squeeze: Price strongly rising over 24h, yet funding remains negative (shorts trapped & paying)
        # Add RSI check to ensure momentum isn't already exhausted
        if price_ret > self.min_ret_pct and funding < -self.funding_threshold and rsi < 70.0:
            if not ctx.has_position() or ctx.position_direction() != "long":
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.5 + abs(funding) * 2000.0 + (price_ret * 5.0))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_squeeze_continuation",
                        "price_ret_24h": round(price_ret, 4),
                        "funding_rate": funding,
                        "rsi": round(rsi, 2),
                        "close": curr_price,
                    },
                )

        # Short Squeeze: Price falling over 24h, yet funding stays positive (longs trapped & paying)
        elif price_ret < -self.min_ret_pct and funding > self.funding_threshold and rsi > 30.0:
            if not ctx.has_position() or ctx.position_direction() != "short":
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.5 + abs(funding) * 2000.0 + (abs(price_ret) * 5.0))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_squeeze_flush",
                        "price_ret_24h": round(price_ret, 4),
                        "funding_rate": funding,
                        "rsi": round(rsi, 2),
                        "close": curr_price,
                    },
                )

        # Structural Mean-Reversion Exit: Momentum divergence resolved
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and rsi > 78.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_overbought_exit", "rsi": round(rsi, 2)})
            elif pos_dir == "short" and rsi < 22.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_oversold_exit", "rsi": round(rsi, 2)})

        return None