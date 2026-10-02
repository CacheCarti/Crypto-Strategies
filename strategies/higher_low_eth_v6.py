from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math


class StructuralHigherLowsTrend(Strategy):
    METADATA = {
        "name": "StructuralHigherLowsTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 43200,  # 12h hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 45
        self.pivot_order = 2  # 2 bars before and 2 bars after to confirm a pivot low
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.last_traded_low_idx = -1
        self.active_support_level = 0.0

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _find_swing_lows(self, lows: List[float], lookback: int, order: int) -> List[Tuple[int, float]]:
        # Finds confirmed pivot lows within the lookback window.
        # Pivot low confirmed if lows[i] is strictly lower than order bars before and after.
        swings = []
        n = len(lows)
        start_idx = max(order, n - lookback)
        end_idx = n - order  # Need order bars after to confirm

        for i in range(start_idx, end_idx):
            val = lows[i]
            is_low = True
            for offset in range(1, order + 1):
                if lows[i - offset] <= val or lows[i + offset] <= val:
                    is_low = False
                    break
            if is_low:
                swings.append((i, val))
        return swings

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.active_support_level = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)

        if len(closes) < self.lookback:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        ema21 = self._ema(closes, 21)
        if ema21 is None:
            return None

        # Position Management & Structural Break Exit
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit if price breaks below the higher low support structure
                if self.active_support_level > 0 and current_close < self.active_support_level:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "structure_breakdown_lower_low",
                            "support_level": round(self.active_support_level, 2),
                            "close": round(current_close, 2),
                            "ema21": round(ema21, 2),
                        },
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime and sentiment filter
        fg_index = ctx.features.get("fear_greed_index", 50)
        if fg_index < 25:  # Skip extreme market fear / capitulation crashes
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Identify swing lows in recent window
        swings = self._find_swing_lows(lows, self.lookback, self.pivot_order)
        if len(swings) < 2:
            return None

        # Take the two most recent confirmed swing lows
        low1_idx, low1_price = swings[-2]
        low2_idx, low2_price = swings[-1]

        # Structural condition: Stair-step higher low
        # 1. Recent swing low must be strictly higher than previous swing low (at least 20 bps higher)
        # 2. Must be separated in time (at least 4 bars apart)
        # 3. Prevent re-entering on the exact same pattern
        is_higher_low = low2_price > (low1_price * 1.002)
        is_well_spaced = (low2_idx - low1_idx) >= 4
        is_new_pattern = low2_idx != self.last_traded_low_idx

        if not (is_higher_low and is_well_spaced and is_new_pattern):
            return None

        # Confirmation trigger: Price is turning up, above EMA21, and candle is bullish
        price_turned_up = current_close > current_open and current_close > ema21
        low2_held = current_close > low2_price

        if price_turned_up and low2_held:
            self.last_traded_low_idx = low2_idx
            self.last_entry_bar = ctx.bar_index
            self.active_support_level = low2_price

            # Confidence scaled by trend steepness and alignment with EMA
            hl_diff_pct = (low2_price - low1_price) / low1_price
            ema_dist_pct = (current_close - ema21) / ema21
            base_conf = 0.65
            if hl_diff_pct > 0.01:
                base_conf += 0.1
            if ema_dist_pct > 0.005:
                base_conf += 0.05
            confidence = min(0.9, max(0.5, base_conf))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "stair_step_higher_low_confirmed",
                    "low1_price": round(low1_price, 2),
                    "low2_price": round(low2_price, 2),
                    "close": round(current_close, 2),
                    "ema21": round(ema21, 2),
                    "fear_greed": fg_index,
                    "bars_between_lows": low2_idx - low1_idx,
                },
            )

        return None