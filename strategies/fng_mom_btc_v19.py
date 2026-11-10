from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_hold_bars = 4

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(45)
        highs = ctx.highs(45)
        lows = ctx.lows(45)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after prior trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        fng = float(ctx.features.get("fear_greed_index", 50.0))
        # Filter: Avoid violent reversal zones at extremes
        sentiment_ok = 25.0 <= fng <= 75.0

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema_fast is None or ema_slow is None or atr is None:
            return None

        # Prior bar EMA values to detect slope / fresh crossover
        prev_ema_fast = self._ema(closes[:-1], self.ema_fast_period)
        prev_ema_slow = self._ema(closes[:-1], self.ema_slow_period)

        if prev_ema_fast is None or prev_ema_slow is None:
            return None

        price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Dynamic stop calculation based on ATR (in bps)
        atr_bps = (atr / price) * 10000.0 if price > 0 else 200.0
        sl_bps = max(160.0, min(320.0, atr_bps * 1.5))
        tp_bps = max(320.0, min(650.0, atr_bps * 3.0))

        # Position management / Exit logic
        if has_pos:
            bars_held = 0
            if ctx.position and "bars_held" in ctx.position:
                bars_held = ctx.position.get("bars_held", 0)

            # Trend exhaustion or sentiment hitting extremes exits active trades
            if pos_dir == "long":
                if ema_fast < ema_slow or fng > 80.0:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_momentum_exhaustion" if fng <= 80.0 else "extreme_greed_exit",
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "fng": fng
                    })
            elif pos_dir == "short":
                if ema_fast > ema_slow or fng < 20.0:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_momentum_exhaustion" if fng >= 20.0 else "extreme_fear_exit",
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "fng": fng
                    })
            return None

        # Entry logic: Fresh transition or pull-back entry in trend
        if not sentiment_ok:
            return None

        # Bullish setup: EMA fast above slow, both trending up, fresh cross or breakout
        bullish_cross = (prev_ema_fast <= prev_ema_slow) and (ema_fast > ema_slow)
        bullish_continuation = (ema_fast > ema_slow) and (price > ema_fast) and (closes[-2] <= prev_ema_fast)
        
        # Bearish setup: EMA fast below slow, both trending down, fresh cross or breakdown
        bearish_cross = (prev_ema_fast >= prev_ema_slow) and (ema_fast < ema_slow)
        bearish_continuation = (ema_fast < ema_slow) and (price < ema_fast) and (closes[-2] >= prev_ema_fast)

        # Baseline confidence scaled mildly with distance from neutral 50 sentiment
        if bullish_cross or bullish_continuation:
            conf = 0.65 + min(0.25, max(0.0, (fng - 50.0) / 100.0))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_cross" if bullish_cross else "bullish_pullback_bounce",
                    "fng": fng,
                    "price": price,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        if bearish_cross or bearish_continuation:
            conf = 0.65 + min(0.25, max(0.0, (50.0 - fng) / 100.0))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_cross" if bearish_cross else "bearish_pullback_reject",
                    "fng": fng,
                    "price": price,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        return None