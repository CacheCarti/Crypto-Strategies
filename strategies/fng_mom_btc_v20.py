from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentMomentumFilter(Strategy):
    METADATA = {
        "name": "SentimentMomentumBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
        return val

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
        closes = ctx.closes(70)
        if len(closes) < 60:
            return None

        # Check cooldown to prevent overtrading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        fng = ctx.features.get("fear_greed_index", 50.0)
        # Avoid extreme sentiment bubbles/panics where trend reversals violently happen
        if not (25.0 <= fng <= 75.0):
            return None

        # Compute EMA20, previous EMA20, and EMA50 for baseline trend alignment
        ema20_curr = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)

        if ema20_curr is None or ema20_prev is None or ema50 is None:
            return None

        # Volatility filter using ATR to avoid stagnant consolidation chop
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None
        
        atr_pct = (atr / closes[-1]) * 10000.0
        # Require healthy volatility: minimum 30 bps per bar
        if atr_pct < 30.0:
            return None

        price = ctx.bar.close
        ema20_slope = ema20_curr - ema20_prev

        # Long Setup: Price above EMA20, EMA20 rising, supported above EMA50, with recent break above EMA20
        # Trigger on fresh cross or strong momentum impulse to avoid firing on every persistent bar
        prev_price = closes[-2]
        just_crossed_above = prev_price <= ema20_prev and price > ema20_curr
        strong_push_up = price > ema20_curr and closes[-1] > closes[-2] > closes[-3]

        if price > ema20_curr and ema20_curr > ema50 and ema20_slope > 0:
            if just_crossed_above or (strong_push_up and not ctx.has_position()):
                conf = min(0.85, 0.55 + (fng - 50.0) / 100.0 if fng >= 50 else 0.60)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sentiment_mid_momentum_bull_expansion",
                        "price": price,
                        "ema20": ema20_curr,
                        "ema50": ema50,
                        "fng": fng,
                        "atr_bps": atr_pct,
                    },
                )

        # Short Setup: Price below EMA20, EMA20 falling, resistance below EMA50
        just_crossed_below = prev_price >= ema20_prev and price < ema20_curr
        strong_push_down = price < ema20_curr and closes[-1] < closes[-2] < closes[-3]

        if price < ema20_curr and ema20_curr < ema50 and ema20_slope < 0:
            if just_crossed_below or (strong_push_down and not ctx.has_position()):
                conf = min(0.85, 0.55 + (50.0 - fng) / 100.0 if fng <= 50 else 0.60)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sentiment_mid_momentum_bear_expansion",
                        "price": price,
                        "ema20": ema20_curr,
                        "ema50": ema50,
                        "fng": fng,
                        "atr_bps": atr_pct,
                    },
                )

        return None