from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class EthRsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 20

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_series = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        if avg_loss == 0.0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
            
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def _find_pivot_lows(self, lows: List[float], rsis: List[float], span: int = 2) -> List[Tuple[int, float, float]]:
        pivots = []
        n = len(lows)
        for i in range(span, n - span):
            is_pivot = True
            for k in range(1, span + 1):
                if lows[i] >= lows[i - k] or lows[i] >= lows[i + k]:
                    is_pivot = False
                    break
            if is_pivot:
                pivots.append((i, lows[i], rsis[i]))
        return pivots

    def _find_pivot_highs(self, highs: List[float], rsis: List[float], span: int = 2) -> List[Tuple[int, float, float]]:
        pivots = []
        n = len(highs)
        for i in range(span, n - span):
            is_pivot = True
            for k in range(1, span + 1):
                if highs[i] <= highs[i - k] or highs[i] <= highs[i + k]:
                    is_pivot = False
                    break
            if is_pivot:
                pivots.append((i, highs[i], rsis[i]))
        return pivots

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.lookback + self.rsi_period + 5
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)
        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if not rsi_vals:
            return None

        current_rsi = rsi_vals[-1]
        
        # Position management: Time stop or RSI normalization
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.entry_bar
            
            # Normalization exit or max hold time
            if direction == "long" and (current_rsi >= 58.0 or bars_in_trade >= self.max_hold_bars):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_rsi_normalized_or_timeout", "rsi": current_rsi, "bars_in_trade": bars_in_trade}
                )
            elif direction == "short" and (current_rsi <= 42.0 or bars_in_trade >= self.max_hold_bars):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_rsi_normalized_or_timeout", "rsi": current_rsi, "bars_in_trade": bars_in_trade}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip during severe market crisis to protect capital
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Work on window of length self.lookback
        w_lows = lows[-self.lookback:]
        w_highs = highs[-self.lookback:]
        w_rsis = rsi_vals[-self.lookback:]

        # Detect pivots (span 2 ensures reliable structural turning points)
        pivot_lows = self._find_pivot_lows(w_lows, w_rsis, span=2)
        pivot_highs = self._find_pivot_highs(w_highs, w_rsis, span=2)

        # Bullish Divergence check: Price lower low, RSI higher low, trigger when RSI emerges from oversold
        if len(pivot_lows) >= 2:
            p1_idx, p1_price, p1_rsi = pivot_lows[-2]
            p2_idx, p2_price, p2_rsi = pivot_lows[-1]
            
            # Recency guard: the latest confirmed pivot must be within the last 6 bars
            if (len(w_lows) - 1 - p2_idx) <= 6:
                if p2_price < p1_price and p2_rsi > (p1_rsi + 2.0) and p1_rsi < 38.0 and current_rsi < 48.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_price": p1_price,
                            "p2_price": p2_price,
                            "p1_rsi": p1_rsi,
                            "p2_rsi": p2_rsi,
                        }
                    )

        # Bearish Divergence check: Price higher high, RSI lower high, trigger when RSI emerges from overbought
        if len(pivot_highs) >= 2:
            p1_idx, p1_price, p1_rsi = pivot_highs[-2]
            p2_idx, p2_price, p2_rsi = pivot_highs[-1]
            
            if (len(w_highs) - 1 - p2_idx) <= 6:
                if p2_price > p1_price and p2_rsi < (p1_rsi - 2.0) and p1_rsi > 62.0 and current_rsi > 52.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_price": p1_price,
                            "p2_price": p2_price,
                            "p1_rsi": p1_rsi,
                            "p2_rsi": p2_rsi,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index