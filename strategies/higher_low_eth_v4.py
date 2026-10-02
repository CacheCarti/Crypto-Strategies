from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class StructuralUptrend(Strategy):
    METADATA = {
        "name": "Structural Uptrend Staircase",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 44
        self.swing_radius = 2
        self.min_pivot_sep = 5
        self.max_pivot_sep = 28
        self.min_hl_pct = 0.0025
        self.ema_period = 14
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.active_structure_low = 0.0

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
        if len(lows) < self.lookback_bars:
            return []
        window = lows[-self.lookback_bars:]
        pivots = []
        for i in range(self.swing_radius, len(window) - self.swing_radius):
            val = window[i]
            is_min = True
            for offset in range(-self.swing_radius, self.swing_radius + 1):
                if offset != 0 and window[i + offset] <= val:
                    is_min = False
                    break
            if is_min:
                bars_ago = len(window) - 1 - i
                pivots.append((bars_ago, val))
        pivots.sort(key=lambda x: x[0])
        return pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.active_structure_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 10)
        lows = ctx.lows(self.lookback_bars + 10)
        highs = ctx.highs(self.lookback_bars + 10)
        if len(closes) < self.lookback_bars:
            return None

        current_close = ctx.bar.close
        current_low = ctx.bar.low
        ema_val = self._ema(closes, self.ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if ctx.has_position():
            if self.active_structure_low > 0.0 and current_close < self.active_structure_low:
                return ctx.signal("flat", confidence=0.85, metadata={
                    "reason": "structural_lower_low_breakdown",
                    "price": current_close,
                    "structure_low": self.active_structure_low
                })
            if rsi_val is not None and rsi_val > 78.0 and current_close < highs[-2]:
                return ctx.signal("flat", confidence=0.60, metadata={
                    "reason": "rsi_overbought_exhaustion",
                    "price": current_close,
                    "rsi": rsi_val
                })
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None
        if crisis_score > 0.60:
            return None

        pivots = self._find_swing_lows(lows)
        if len(pivots) < 2:
            return None

        new_pivot_bars_ago, new_pivot_price = pivots[0]
        old_pivot_bars_ago, old_pivot_price = pivots[1]

        if not (2 <= new_pivot_bars_ago <= 9):
            return None

        pivot_dist = old_pivot_bars_ago - new_pivot_bars_ago
        if not (self.min_pivot_sep <= pivot_dist <= self.max_pivot_sep):
            return None

        if new_pivot_price <= old_pivot_price * (1.0 + self.min_hl_pct):
            return None

        if ema_val is None or current_close <= ema_val:
            return None

        if closes[-1] <= closes[-2]:
            return None

        if rsi_val is None or rsi_val < 42.0 or rsi_val > 68.0:
            return None

        higher_low_pct = (new_pivot_price - old_pivot_price) / old_pivot_price * 100.0
        confidence = min(0.90, max(0.60, 0.65 + (higher_low_pct * 0.05)))

        self.last_entry_bar = ctx.bar_index
        self.active_structure_low = new_pivot_price

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "higher_swing_low_structure_confirmed",
                "price": current_close,
                "ema": ema_val,
                "rsi": rsi_val,
                "l1_old_low": old_pivot_price,
                "l2_new_low": new_pivot_price,
                "bars_since_l2": new_pivot_bars_ago,
                "pivot_separation": pivot_dist
            }
        )