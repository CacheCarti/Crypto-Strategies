from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleTestSupportBounce(Strategy):
    METADATA = {
        "name": "Triple Test Support Bounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # 8 hours
        "warmup_bars": 70,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 60
        self.tolerance_pct = 0.0040       # Tight 0.40% zone above support
        self.cooldown_bars = 24           # 24-bar cooldown to prevent overtrading
        self.last_exit_bar = -100
        self.min_touch_spacing = 10       # Minimum 10 bars between distinct touches

    def _find_support_and_touches(self, lows: list, closes: list) -> tuple:
        # Identify the swing support level across the lookback period
        history_lows = lows[-self.lookback:-1]
        support_level = min(history_lows)
        zone_top = support_level * (1.0 + self.tolerance_pct)

        # Count distinct trough touches separated by at least min_touch_spacing bars
        touches = []
        last_touch_idx = -100

        for idx, (l, c) in enumerate(zip(history_lows, closes[-self.lookback:-1])):
            if l <= zone_top and c >= support_level:
                if (idx - last_touch_idx) >= self.min_touch_spacing:
                    touches.append(idx)
                    last_touch_idx = idx

        return support_level, zone_top, len(touches)

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

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)
        opens = ctx.opens(self.lookback + 10)

        if len(closes) < self.lookback + 5:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_low = ctx.bar.low
        current_high = ctx.bar.high

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                rsi_curr = self._rsi(closes, 14) or 50.0
                # Take profit early if RSI hits deep overbought
                if rsi_curr >= 76.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "rsi_overbought_exit",
                            "rsi": round(rsi_curr, 2),
                            "close": round(current_close, 2)
                        }
                    )
            return None

        # Hard cooldown check after exit to preserve capital against friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filters: skip during high market stress or severe crash
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Funding rate filter: avoid long crowded tops
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        if funding_rate > 0.0005:
            return None

        # Trend filter: ensure intermediate trend is not in a catastrophic freefall
        ema50 = self._ema(closes, 50)
        if ema50 is not None and current_close < ema50 * 0.94:
            return None

        support_level, zone_top, prior_touches = self._find_support_and_touches(lows, closes)

        # Look specifically for structural 3rd test (2 prior valid touches)
        if prior_touches == 2:
            tested_zone = (current_low <= zone_top)
            held_support = (current_close >= support_level)
            
            # Candle confirmation: green candle with a clear rejection lower wick
            candle_range = max(current_high - current_low, 0.01)
            lower_wick = min(current_open, current_close) - current_low
            rejection_candle = (current_close > current_open) and (lower_wick / candle_range >= 0.25)

            if tested_zone and held_support and rejection_candle:
                rsi_val = self._rsi(closes, 14) or 50.0
                rsi_prev = self._rsi(closes[:-1], 14) or 50.0

                # RSI must be recovering from oversold/neutral territory
                if 28.0 <= rsi_val <= 55.0 and rsi_val > rsi_prev:
                    distance_to_sl = (current_close - support_level) / current_close
                    sl_bps = max(180.0, min(320.0, (distance_to_sl * 10000.0) + 50.0))
                    tp_bps = max(360.0, sl_bps * 2.0)

                    confidence = 0.70
                    if rsi_val <= 40.0:
                        confidence += 0.15
                    if funding_rate <= 0.0:
                        confidence += 0.10
                    confidence = min(1.0, confidence)

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=28800,
                        metadata={
                            "reason": "triple_test_support_bounce_confirmed",
                            "support_level": round(support_level, 2),
                            "zone_top": round(zone_top, 2),
                            "prior_touches": prior_touches,
                            "rsi": round(rsi_val, 2),
                            "funding_rate": round(funding_rate, 6),
                            "close": round(current_close, 2)
                        }
                    )

        return None