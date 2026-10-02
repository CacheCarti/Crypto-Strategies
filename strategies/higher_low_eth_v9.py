from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StructuralHigherLows(Strategy):
    METADATA = {
        "name": "Structural Higher Lows",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 60
        self.k_pivot = 4
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.last_traded_l2_idx = -1
        self.active_structure_low = 0.0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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

    def _find_swing_lows(self, lows, lookback: int, k: int):
        n = len(lows)
        if n < lookback:
            return []

        start_idx = max(k, n - lookback)
        end_idx = n - k
        swing_lows = []

        for i in range(start_idx, end_idx):
            val = lows[i]
            is_pivot = True
            for offset in range(-k, k + 1):
                if offset != 0 and lows[i + offset] <= val:
                    is_pivot = False
                    break
            if is_pivot:
                swing_lows.append((i, val))
        return swing_lows

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        lows = ctx.lows(self.lookback + 20)
        highs = ctx.highs(self.lookback + 20)

        if len(closes) < self.METADATA["warmup_bars"] or len(lows) < self.METADATA["warmup_bars"]:
            return None

        # Manage open position exit condition (structure breakdown)
        if ctx.has_position():
            curr_close = closes[-1]
            if self.active_structure_low > 0 and curr_close < self.active_structure_low:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_breakdown_below_swing_low",
                        "active_structure_low": self.active_structure_low,
                        "close": curr_close,
                    },
                )
            return None

        # Mandatory multi-bar cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Market regime and sentiment filters
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear":
            return None

        fg_index = ctx.features.get("fear_greed_index", 50)
        if fg_index < 25:
            return None

        # Find confirmed swing lows with k_pivot spacing
        swing_lows = self._find_swing_lows(lows, lookback=self.lookback, k=self.k_pivot)
        if len(swing_lows) < 2:
            return None

        l1_idx, l1_val = swing_lows[-2]
        l2_idx, l2_val = swing_lows[-1]

        # Prevent re-trading the exact same swing low pair
        if l2_idx == self.last_traded_l2_idx:
            return None

        bars_since_l2 = (len(lows) - 1) - l2_idx
        spacing = l2_idx - l1_idx

        # Timing window: trigger only right when the pivot is confirmed
        if spacing < 8 or spacing > 35:
            return None
        if bars_since_l2 < self.k_pivot or bars_since_l2 > self.k_pivot + 4:
            return None

        # Second low must be meaningfully higher (at least 40 bps stair-step)
        if l2_val < l1_val * 1.004:
            return None

        # Trend & Momentum alignment filters
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)
        curr_close = closes[-1]
        prev_close = closes[-2]

        if ema20 is None or ema50 is None or rsi is None:
            return None

        # Price must be above EMA20, EMA20 above EMA50, and RSI in healthy momentum zone
        if curr_close <= prev_close or curr_close < ema20 or ema20 < ema50:
            return None
        if rsi < 48.0 or rsi > 70.0:
            return None

        # Record trade setup to prevent duplicate triggers on same structure
        self.last_entry_bar = ctx.bar_index
        self.last_traded_l2_idx = l2_idx
        self.active_structure_low = l2_val

        gain_bps = ((l2_val - l1_val) / l1_val) * 10000.0
        confidence = min(0.9, max(0.6, 0.6 + (gain_bps / 500.0) * 0.2))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "two_consecutive_higher_swing_lows_confirmed",
                "l1_val": l1_val,
                "l2_val": l2_val,
                "l1_idx": l1_idx,
                "l2_idx": l2_idx,
                "bars_since_l2": bars_since_l2,
                "close": curr_close,
                "ema20": ema20,
                "ema50": ema50,
                "rsi": rsi,
                "fg_index": fg_index,
            },
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.active_structure_low = 0.0