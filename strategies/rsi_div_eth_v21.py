from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 3
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.bars_in_pos = 0

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        rsi_series = []
        for end_idx in range(period + 1, len(closes) + 1):
            sub = closes[:end_idx]
            gains, losses = [], []
            for i in range(1, len(sub)):
                diff = sub[i] - sub[i - 1]
                gains.append(max(diff, 0.0))
                losses.append(max(-diff, 0.0))
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def _find_pivots(self, series: List[float], window: int) -> List[int]:
        pivots_low = []
        pivots_high = []
        n = len(series)
        for i in range(window, n - window):
            val = series[i]
            if val == min(series[i - window : i + window + 1]):
                pivots_low.append(i)
            if val == max(series[i - window : i + window + 1]):
                pivots_high.append(i)
        return pivots_low, pivots_high

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(needed)
        if len(closes) < needed:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        current_close = closes[-1]
        c_sub = closes[-len(rsi_series):]

        if ctx.has_position():
            self.bars_in_pos += 1
            pos_dir = ctx.position_direction()
            
            # Normalization / Mean reversion exits
            if pos_dir == "long" and (current_rsi >= 58.0 or self.bars_in_pos >= 10):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_rsi_normalized_or_time", "rsi": current_rsi, "bars_held": self.bars_in_pos})
            elif pos_dir == "short" and (current_rsi <= 42.0 or self.bars_in_pos >= 10):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_rsi_normalized_or_time", "rsi": current_rsi, "bars_held": self.bars_in_pos})
            return None

        self.bars_in_pos = 0

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        pivots_low, pivots_high = self._find_pivots(rsi_series, self.pivot_window)

        # Bullish Divergence Detection
        # Price lower low, RSI higher low, RSI currently in oversold/rebound territory (< 44)
        if len(pivots_low) >= 2 and current_rsi < 44.0:
            p1, p2 = pivots_low[-2], pivots_low[-1]
            if (len(rsi_series) - 1 - p2) <= self.pivot_window + 2:
                rsi1, rsi2 = rsi_series[p1], rsi_series[p2]
                price1, price2 = c_sub[p1], c_sub[p2]
                if price2 < price1 * 0.998 and rsi2 > rsi1 + 1.5:
                    self.last_trade_bar = ctx.bar_index
                    conf = min(0.85, 0.55 + (rsi2 - rsi1) * 0.02)
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "rsi1": rsi1,
                            "rsi2": rsi2,
                            "price1": price1,
                            "price2": price2,
                        }
                    )

        # Bearish Divergence Detection
        # Price higher high, RSI lower high, RSI currently in overbought/rollover territory (> 56)
        if len(pivots_high) >= 2 and current_rsi > 56.0:
            p1, p2 = pivots_high[-2], pivots_high[-1]
            if (len(rsi_series) - 1 - p2) <= self.pivot_window + 2:
                rsi1, rsi2 = rsi_series[p1], rsi_series[p2]
                price1, price2 = c_sub[p1], c_sub[p2]
                if price2 > price1 * 1.002 and rsi2 < rsi1 - 1.5:
                    self.last_trade_bar = ctx.bar_index
                    conf = min(0.85, 0.55 + (rsi1 - rsi2) * 0.02)
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "rsi1": rsi1,
                            "rsi2": rsi2,
                            "price1": price1,
                            "price2": price2,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.bars_in_pos = 0