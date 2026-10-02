from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class StructuralUptrendStrategy(Strategy):
    METADATA = {
        "name": "Structural Swing Low Uptrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 46
        self.pivot_span = 2  # 2 bars left & right for swing pivot
        self.min_pivot_distance = 6
        self.cooldown_bars = 7
        self.last_exit_bar = -999
        self.entry_structure_low = 0.0
        self.bars_in_trade = 0

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def _find_swing_lows(self, lows: List[float]) -> List[Tuple[int, float]]:
        """Identify local swing low pivots with index relative to recent window."""
        n = len(lows)
        pivots = []
        span = self.pivot_span
        for i in range(span, n - span):
            val = lows[i]
            is_pivot = True
            for offset in range(1, span + 1):
                if lows[i - offset] <= val or lows[i + offset] < val:
                    is_pivot = False
                    break
            if is_pivot:
                pivots.append((i, val))
        return pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_structure_low = 0.0
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback)
        highs = ctx.highs(self.lookback)
        lows = ctx.lows(self.lookback)
        opens = ctx.opens(self.lookback)

        if len(closes) < self.lookback:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        ema21 = self._ema(closes, 21)
        rsi = self._rsi(closes, 14)

        if ema21 is None or rsi is None:
            return None

        # Position Management & Invalidation Check
        if ctx.has_position():
            self.bars_in_trade += 1

            # Exit condition 1: Break below the structural higher low (structure invalidated)
            if self.entry_structure_low > 0.0 and current_close < self.entry_structure_low:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_invalidation_break_below_swing_low",
                        "close": current_close,
                        "structure_low": self.entry_structure_low,
                        "rsi": round(rsi, 2),
                    },
                )

            # Exit condition 2: Overbought momentum exhaustion reversal
            if rsi > 76.0 and current_close < current_open:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "momentum_exhaustion_overbought",
                        "close": current_close,
                        "rsi": round(rsi, 2),
                    },
                )

            # Exit condition 3: Time horizon cap for swing setup
            if self.bars_in_trade >= 24:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_reached",
                        "bars_held": self.bars_in_trade,
                        "close": current_close,
                    },
                )

            return None

        # Cooldown guard after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 20.0:  # Skip severe panic capitulations
            return None

        # Structural higher-low detection
        pivots = self._find_swing_lows(lows)
        if len(pivots) < 2:
            return None

        idx1, low1 = pivots[-2]
        idx2, low2 = pivots[-1]

        # Ensure minimum bar separation between the two swing lows
        if (idx2 - idx1) < self.min_pivot_distance:
            return None

        # The second low must be higher than the first low (stair-step structure)
        # and must not be too distant from current bar (recent test and hold)
        bars_since_second_low = (len(lows) - 1) - idx2
        is_higher_low = low2 > low1 * 1.002
        is_recent_hold = 2 <= bars_since_second_low <= 10

        if not (is_higher_low and is_recent_hold):
            return None

        # Entry trigger: price above EMA21, bullish candle body, healthy momentum
        turn_up = current_close > current_open and current_close > ema21
        healthy_momentum = 46.0 <= rsi <= 68.0

        if is_higher_low and turn_up and healthy_momentum:
            # Dynamic stop loss based on distance to structural swing low
            distance_to_low_bps = ((current_close - low2) / current_close) * 10000.0
            sl_bps = max(180.0, min(distance_to_low_bps + 30.0, 340.0))
            tp_bps = max(sl_bps * 2.0, 480.0)

            # Confidence scaled by trend alignment and structure quality
            structure_margin = (low2 - low1) / low1
            confidence = 0.65 + min(0.25, structure_margin * 10.0)

            self.entry_structure_low = low2
            self.bars_in_trade = 0

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=21600,
                metadata={
                    "reason": "two_higher_swing_lows_confirmed_turn_up",
                    "swing_low_1": round(low1, 2),
                    "swing_low_2": round(low2, 2),
                    "bars_since_l2": bars_since_second_low,
                    "ema21": round(ema21, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "price": current_close,
                },
            )

        return None