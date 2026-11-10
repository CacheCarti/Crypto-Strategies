from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold on 1h bars
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema_series(self, values: list, period: int) -> list:
        if len(values) < period:
            return []
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        emas = [ema]
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
            emas.append(ema)
        return emas

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard: prevent overtrading and chop churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate EMA series to detect trend and momentum slope
        ema_vals = self._ema_series(closes, self.ema_period)
        if len(ema_vals) < 3:
            return None

        curr_ema = ema_vals[-1]
        prev_ema = ema_vals[-2]
        ema_slope = curr_ema - prev_ema

        price = closes[-1]
        prev_price = closes[-2]

        # Sentiment regime gating: stand down at violent blow-off/capitulation extremes (<25 or >75)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng is None or fng < 25.0 or fng > 75.0:
            return None

        # Dynamic volatility-adjusted SL/TP
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr and price > 0:
            atr_bps = (atr / price) * 10000.0
            sl_bps = max(180.0, min(350.0, atr_bps * 1.5))
            tp_bps = sl_bps * 2.0
        else:
            sl_bps = 250.0
            tp_bps = 500.0

        # Confidence scaled by sentiment normalization (peaks near 50 neutral sentiment)
        dist_from_mid = abs(fng - 50.0)
        confidence = round(max(0.55, 0.85 - (dist_from_mid / 50.0) * 0.25), 2)

        # Transition-based entry triggers to avoid repeated firing during prolonged trends
        long_cross = (prev_price <= prev_ema and price > curr_ema)
        short_cross = (prev_price >= prev_ema and price < curr_ema)

        # Trend continuation breakout trigger (breaks prior 5-bar high/low with aligned slope)
        recent_high = max(highs[-6:-1])
        recent_low = min(lows[-6:-1])
        long_breakout = (price > recent_high and price > curr_ema and ema_slope > 0)
        short_breakout = (price < recent_low and price < curr_ema and ema_slope < 0)

        if not ctx.has_position():
            # Long trigger: Crossing above rising EMA or breakout in moderate sentiment
            if (long_cross and ema_slope > 0) or long_breakout:
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "sentiment_conditioned_mom_long",
                        "fng": fng,
                        "price": price,
                        "ema20": curr_ema,
                        "ema_slope": ema_slope,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps,
                    },
                )

            # Short trigger: Crossing below falling EMA or breakdown in moderate sentiment
            if (short_cross and ema_slope < 0) or short_breakout:
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "sentiment_conditioned_mom_short",
                        "fng": fng,
                        "price": price,
                        "ema20": curr_ema,
                        "ema_slope": ema_slope,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps,
                    },
                )

        return None