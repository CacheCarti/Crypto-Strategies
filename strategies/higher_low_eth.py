from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class StructuralHigherLowUptrend(Strategy):
    METADATA = {
        "name": "Structural Higher Low Uptrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window_bars = 42
        self.pivot_span = 2
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.last_swing_low_level = 0.0

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
        """Identifies pivot lows in the recent window. Returns list of (bar_offset_from_end, price)."""
        pivots: List[Tuple[int, float]] = []
        n = len(lows)
        start_idx = max(self.pivot_span, n - self.window_bars)
        end_idx = n - self.pivot_span

        for i in range(start_idx, end_idx):
            val = lows[i]
            is_pivot = True
            for offset in range(1, self.pivot_span + 1):
                if lows[i - offset] <= val or lows[i + offset] < val:
                    is_pivot = False
                    break
            if is_pivot:
                offset_from_end = n - 1 - i
                pivots.append((offset_from_end, val))
        return pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.last_swing_low_level = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(55)
        lows = ctx.lows(55)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)

        if ema20 is None or ema50 is None or rsi is None:
            return None

        # --- Position Management & Structural Exit ---
        if ctx.has_position():
            # Invalidation exit: Price breaks below the recent structural swing low support
            if self.last_swing_low_level > 0 and current_price < self.last_swing_low_level:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_break_lower_low",
                        "price": current_price,
                        "swing_low_support": self.last_swing_low_level,
                        "rsi": rsi,
                    },
                )

            # Overextended exhaustion exit
            if rsi > 78.0 and current_price < ctx.bar.open:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_overbought_reversal",
                        "rsi": rsi,
                        "price": current_price,
                    },
                )
            return None

        # --- Cooldown Check ---
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # --- Structural Analysis: 2 Consecutive Higher Swing Lows ---
        swing_lows = self._find_swing_lows(lows)
        if len(swing_lows) < 2:
            return None

        # Get last two pivot lows: older (sw1) and recent (sw2)
        sw2_offset, sw2_price = swing_lows[-1]
        sw1_offset, sw1_price = swing_lows[-2]

        # Ensure sw2 is more recent than sw1 and formed a confirmed higher low (at least 0.15% above sw1)
        higher_low_formed = (sw2_offset < sw1_offset) and (sw2_price >= sw1_price * 1.0015)
        # Ensure the second swing low is relatively fresh (formed within the last 15 bars)
        fresh_structure = 2 <= sw2_offset <= 16

        if not (higher_low_formed and fresh_structure):
            return None

        # --- Momentum & Confirmation Filter ---
        # Price turns upward above EMA20, RSI in healthy expansion range (42 - 68)
        price_above_support = current_price > sw2_price
        trend_aligned = current_price >= ema20 and ema20 >= (ema50 * 0.995)
        rsi_healthy = 42.0 <= rsi <= 68.0
        bullish_candle = current_price >= ctx.bar.open or closes[-1] > closes[-2]

        if price_above_support and trend_aligned and rsi_healthy and bullish_candle:
            self.last_entry_bar = ctx.bar_index
            self.last_swing_low_level = sw2_price

            confidence = 0.65
            if current_price > ema50:
                confidence += 0.15
            if sw2_price > sw1_price * 1.006:
                confidence += 0.10
            confidence = min(1.0, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "two_higher_swing_lows_confirmed",
                    "sw1_price": sw1_price,
                    "sw2_price": sw2_price,
                    "price": current_price,
                    "ema20": ema20,
                    "rsi": rsi,
                },
            )

        return None