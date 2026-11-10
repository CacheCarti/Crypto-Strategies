from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid violent market regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        # Indicator calculations
        ema20_curr = self._ema(closes, self.fast_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)

        if ema20_curr is None or ema20_prev is None or ema50_curr is None:
            return None

        price = ctx.bar.close
        prev_price = closes[-2]
        ema20_slope = (ema20_curr - ema20_prev) / ema20_prev

        # Dynamic exit / position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if price < ema20_curr and ema20_slope < 0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_ema20_bear_inflection", "price": price, "ema20": ema20_curr})
            elif pos_dir == "short":
                if price > ema20_curr and ema20_slope > 0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_ema20_bull_inflection", "price": price, "ema20": ema20_curr})
            return None

        # Sentiment gate: avoid extreme reversals (<25 extreme fear, >75 extreme greed)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if not (25.0 <= fng <= 75.0):
            return None

        # Trend and entry condition: require transition or clean breakout to avoid continuous triggers
        bull_structure = ema20_curr > ema50_curr and ema20_slope > 0.0003
        bear_structure = ema20_curr < ema50_curr and ema20_slope < -0.0003

        long_trigger = bull_structure and prev_price <= ema20_prev and price > ema20_curr
        short_trigger = bear_structure and prev_price >= ema20_prev and price < ema20_curr

        if long_trigger:
            conf = min(0.85, 0.55 + abs(ema20_slope) * 200)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_momentum_long",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "ema20_slope": ema20_slope,
                    "fng": fng
                }
            )

        if short_trigger:
            conf = min(0.85, 0.55 + abs(ema20_slope) * 200)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_momentum_short",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "ema20_slope": ema20_slope,
                    "fng": fng
                }
            )

        return None