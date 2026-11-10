from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "Funding Squeeze Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_trade_bar = -999

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 20:
            return None

        # Hard cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        price_ret_24 = (current_close - past_close) / past_close

        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)
        if ema50 is None or rsi14 is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic based on momentum exhaustion
        if has_pos:
            if pos_dir == "long" and (rsi14 > 72.0 or current_close < ema50):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_momentum_exhausted", "rsi": rsi14, "price": current_close})
            elif pos_dir == "short" and (rsi14 < 28.0 or current_close > ema50):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_momentum_exhausted", "rsi": rsi14, "price": current_close})
            return None

        # Entry logic: Price & Funding positioning divergence
        # Long Squeeze: Price actively advancing over 24 bars while funding is persistently negative (shorts paying longs)
        long_squeeze = (price_ret_24 > 0.012) and (funding < -0.00003) and (current_close > ema50) and (rsi14 > 50.0 and rsi14 < 68.0)

        # Short Squeeze: Price sliding over 24 bars while funding is persistently positive (longs paying shorts)
        short_squeeze = (price_ret_24 < -0.012) and (funding > 0.00008) and (current_close < ema50) and (rsi14 < 50.0 and rsi14 > 32.0)

        if long_squeeze:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(funding) * 2000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_short_squeeze_continuation",
                    "funding": funding,
                    "price_ret_24": round(price_ret_24, 4),
                    "rsi14": round(rsi14, 2),
                    "ema50": round(ema50, 2),
                }
            )

        if short_squeeze:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(funding) * 2000.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_long_squeeze_continuation",
                    "funding": funding,
                    "price_ret_24": round(price_ret_24, 4),
                    "rsi14": round(rsi14, 2),
                    "ema50": round(ema50, 2),
                }
            )

        return None