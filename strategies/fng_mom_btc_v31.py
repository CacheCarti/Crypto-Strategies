from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.rsi_period = 14
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -100

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid overtrading
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        market_regime = ctx.market.get("regime", "NORMAL")

        # Stand down at extreme sentiment or crisis conditions
        if fg_index <= 25.0 or fg_index >= 75.0 or market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "extreme_sentiment_or_crisis_exit", "fg_index": fg_index, "regime": market_regime}
                )
            return None

        ema20_curr = self._ema(closes, self.fast_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        rsi_curr = self._rsi(closes, self.rsi_period)
        atr_curr = self._atr(highs, lows, closes, self.atr_period)

        if ema20_curr is None or ema20_prev is None or ema50_curr is None or rsi_curr is None or atr_curr is None:
            return None

        price = ctx.bar.close
        atr_bps = (atr_curr / price) * 10000.0

        # Dynamic stops scaled to volatility
        sl_bps = max(150.0, min(400.0, atr_bps * 1.5))
        tp_bps = max(300.0, min(800.0, sl_bps * 2.0))

        # Check existing position for exit criteria
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (price < ema20_curr or rsi_curr > 75.0):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "long_momentum_exhausted", "price": price, "ema20": ema20_curr, "rsi": rsi_curr}
                )
            elif pos_dir == "short" and (price > ema20_curr or rsi_curr < 25.0):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "short_momentum_exhausted", "price": price, "ema20": ema20_curr, "rsi": rsi_curr}
                )
            return None

        # Entry triggers: Trend confirmation + rising/falling EMA20 + RSI pull-in room
        prev_close = closes[-2]
        long_cross = prev_close <= ema20_prev and price > ema20_curr
        short_cross = prev_close >= ema20_prev and price < ema20_curr

        ema20_slope = (ema20_curr - ema20_prev) / ema20_prev

        # Long Setup: Price above EMA20, EMA20 rising, above EMA50, healthy RSI
        if long_cross and ema20_slope > 0.0002 and ema20_curr > ema50_curr and 48.0 < rsi_curr < 65.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_bullish_momentum_breakout",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "rsi": rsi_curr,
                    "fg_index": fg_index,
                    "atr_bps": atr_bps
                }
            )

        # Short Setup: Price below EMA20, EMA20 falling, below EMA50, healthy RSI
        if short_cross and ema20_slope < -0.0002 and ema20_curr < ema50_curr and 35.0 < rsi_curr < 52.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_bearish_momentum_breakdown",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "rsi": rsi_curr,
                    "fg_index": fg_index,
                    "atr_bps": atr_bps
                }
            )

        return None