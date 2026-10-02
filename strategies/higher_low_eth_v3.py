from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class StructuralSwingUptrend(Strategy):
    METADATA = {
        "name": "Structural Swing Uptrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 45
        self.flank = 2
        self.ema_fast_period = 9
        self.ema_slow_period = 34
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.active_support_level = 0.0

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1.0 - k)
        return ema_val

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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

    def _find_recent_swing_lows(self, lows: List[float]) -> List[tuple]:
        n = len(lows)
        if n < self.lookback:
            return []
        start_idx = n - self.lookback
        swing_lows = []
        for i in range(start_idx + self.flank, n - self.flank):
            val = lows[i]
            is_low = True
            for f in range(1, self.flank + 1):
                if lows[i - f] <= val or lows[i + f] < val:
                    is_low = False
                    break
            if is_low:
                swing_lows.append((i, val))
        return swing_lows

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)
        opens = ctx.opens(self.lookback + 10)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Position Management
        if ctx.has_position():
            # Invalidation exit: closed below structural higher-low support
            if self.active_support_level > 0.0 and current_close < self.active_support_level:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_breakdown_below_l2",
                        "price": current_close,
                        "support": self.active_support_level,
                        "rsi": round(rsi, 2),
                    },
                )

            # Overextension momentum exit
            if rsi > 78.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_overbought_take_profit",
                        "price": current_close,
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Market & Sentiment filters
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear":
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 25.0:
            return None

        # Find swing lows
        swing_lows = self._find_recent_swing_lows(lows)
        if len(swing_lows) < 2:
            return None

        l1_idx, l1_val = swing_lows[-2]
        l2_idx, l2_val = swing_lows[-1]

        # Verify stair-step higher low structure
        is_higher_low = l2_val > (l1_val * 1.002)
        spacing = l2_idx - l1_idx
        recency = (len(lows) - 1) - l2_idx

        if not (is_higher_low and 4 <= spacing <= 28 and 1 <= recency <= 12):
            return None

        # Price trigger: turning up from L2 above fast EMA with bullish candle
        is_bullish_turn = (current_close > ema_fast) and (current_close >= current_open)
        trend_aligned = (current_close > ema_slow * 0.995)
        rsi_valid = 40.0 <= rsi <= 68.0

        if is_bullish_turn and trend_aligned and rsi_valid:
            self.last_entry_bar = ctx.bar_index
            self.active_support_level = l2_val
            confidence = 0.65 if current_close > ema_slow else 0.55

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "higher_low_stair_step_bounce",
                    "price": current_close,
                    "l1_price": l1_val,
                    "l2_price": l2_val,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "spacing_bars": spacing,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.active_support_level = 0.0