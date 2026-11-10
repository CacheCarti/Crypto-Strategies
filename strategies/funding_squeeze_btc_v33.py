from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

        # Cooldown guard: prevent overtrading and friction bleeding
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Price momentum over 24 bars
        p_now = closes[-1]
        p_prev = closes[-self.lookback]
        ret_24 = (p_now - p_prev) / p_prev

        # Trapped positioning divergence via funding rate
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Volatility & regime filters
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Handle active position management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time or exhaustion exits
            if direction == "long" and (rsi > 78.0 or funding > 0.0003 or bars_held >= 20):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_exhaustion_or_max_hold", "rsi": rsi, "funding": funding})
            elif direction == "short" and (rsi < 22.0 or funding < -0.0003 or bars_held >= 20):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_exhaustion_or_max_hold", "rsi": rsi, "funding": funding})
            return None

        # Entry logic: price rises meaningfully while funding stays negative (shorts trapped squeeze)
        long_condition = (ret_24 >= 0.012) and (funding <= -0.00005) and (40.0 <= rsi <= 68.0)

        # Entry logic: price drops meaningfully while funding stays positive (longs trapped squeeze)
        short_condition = (ret_24 <= -0.012) and (funding >= 0.00005) and (32.0 <= rsi <= 60.0)

        if long_condition:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + abs(funding) * 500.0 + min(ret_24 * 5.0, 0.25))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "ret_24": round(ret_24, 4),
                    "funding": funding,
                    "rsi": round(rsi, 2)
                }
            )

        if short_condition:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + abs(funding) * 500.0 + min(abs(ret_24) * 5.0, 0.25))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_squeeze",
                    "ret_24": round(ret_24, 4),
                    "funding": funding,
                    "rsi": round(rsi, 2)
                }
            )

        return None