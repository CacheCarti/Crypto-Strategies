from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class StructuralHigherLowsTrend(Strategy):
    METADATA = {
        "name": "StructuralHigherLowsTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pivot_span = 3
        self.ema_period = 21
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.current_structural_stop = 0.0

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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

    def _find_swing_lows(self, lows: List[float], window: int, span: int) -> List[Tuple[int, float]]:
        total_len = len(lows)
        if total_len < window:
            return []
        start_idx = max(span, total_len - window)
        end_idx = total_len - span
        swings = []
        for i in range(start_idx, end_idx):
            val = lows[i]
            is_min = True
            for offset in range(-span, span + 1):
                if offset != 0 and lows[i + offset] <= val:
                    is_min = False
                    break
            if is_min:
                swings.append((i, val))
        return swings

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.current_structural_stop = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)

        if len(closes) < self.lookback:
            return None

        current_close = closes[-1]
        ema21 = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema21 is None or rsi is None:
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # 1. Manage Active Long Position
        if ctx.has_position():
            # Exit if structure breaks (close falls below the higher low pivot)
            if self.current_structural_stop > 0.0 and current_close < self.current_structural_stop:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_breakdown_below_swing_low",
                        "price": current_close,
                        "stop_level": self.current_structural_stop,
                        "rsi": round(rsi, 2),
                    }
                )

            # Exit if momentum heavily exhausts in overbought territory
            if rsi > 78.0 and current_close < closes[-2]:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "rsi_overbought_exhaustion",
                        "price": current_close,
                        "rsi": round(rsi, 2),
                    }
                )
            return None

        # 2. Check Cooldown Gate
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # 3. Detect Two Consecutive Higher Swing Lows
        swings = self._find_swing_lows(lows, window=self.lookback, span=self.pivot_span)
        if len(swings) < 2:
            return None

        idx1, low1 = swings[-2]
        idx2, low2 = swings[-1]

        # Ensure minimum spacing between swing lows (at least 5 bars apart)
        if (idx2 - idx1) < 5:
            return None

        # Verify second low is strictly higher (clear stair-step structure)
        if low2 <= low1 * 1.0025:
            return None

        # Ensure the second swing low is relatively fresh (formed within last 12 bars)
        bars_since_low2 = len(lows) - 1 - idx2
        if bars_since_low2 > 12:
            return None

        # 4. Confirmation Trigger: Price holds above EMA21 and turns up with healthy RSI
        price_turning_up = current_close > closes[-2] and current_close > ema21
        rsi_in_healthy_band = 40.0 <= rsi <= 66.0

        if price_turning_up and rsi_in_healthy_band:
            # Calculate dynamic stop based on the second swing low
            distance_to_low2_bps = ((current_close - low2) / current_close) * 10000.0
            stop_bps = max(180.0, min(distance_to_low2_bps + 30.0, 380.0))
            take_profit_bps = stop_bps * 2.0

            # Scale confidence by structural spacing and RSI alignment
            confidence = 0.65
            if 48.0 <= rsi <= 58.0:
                confidence += 0.1
            if current_close > highs[-2]:
                confidence += 0.1
            confidence = min(confidence, 0.9)

            self.last_entry_bar = ctx.bar_index
            self.current_structural_stop = low2

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=stop_bps,
                take_profit_bps=take_profit_bps,
                metadata={
                    "reason": "two_higher_lows_stair_step_breakout",
                    "low1": round(low1, 2),
                    "low2": round(low2, 2),
                    "price": round(current_close, 2),
                    "ema21": round(ema21, 2),
                    "rsi": round(rsi, 2),
                    "bars_since_second_low": bars_since_low2,
                    "stop_loss_bps": round(stop_bps, 1),
                }
            )

        return None