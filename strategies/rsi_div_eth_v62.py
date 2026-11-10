from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_trade_bar = -999
        self.bars_in_position = 0

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

    def _find_pivots(self, series: List[float], order: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        lows = []
        highs = []
        n = len(series)
        for i in range(order, n - order):
            val = series[i]
            if all(val < series[j] for j in range(i - order, i)) and all(val <= series[j] for j in range(i + 1, i + order + 1)):
                lows.append((i, val))
            if all(val > series[j] for j in range(i - order, i)) and all(val >= series[j] for j in range(i + 1, i + order + 1)):
                highs.append((i, val))
        return lows, highs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = self.METADATA["warmup_bars"]
        closes = ctx.closes(warmup + 10)
        if len(closes) < warmup:
            return None

        # Manage open position
        if ctx.has_position():
            self.bars_in_position += 1
            current_rsi_list = self._calc_rsi_series(closes[-16:], period=14)
            current_rsi = current_rsi_list[-1] if current_rsi_list else 50.0
            direction = ctx.position_direction()

            # Exit criteria: mean reversion satisfied or time-stop
            if direction == "long" and (current_rsi >= 60.0 or self.bars_in_position >= 12):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_long_exit", "rsi": current_rsi, "bars_held": self.bars_in_position})
            elif direction == "short" and (current_rsi <= 40.0 or self.bars_in_position >= 12):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_short_exit", "rsi": current_rsi, "bars_held": self.bars_in_position})
            return None

        # Post-trade cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid crisis/meltdown where mean-reversion fails
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45:
            return None

        # Calculate historical RSI values for divergence detection
        window = 32
        window_closes = closes[-window:]
        rsi_vals = self._calc_rsi_series(closes[-(window + 15):], period=14)
        if len(rsi_vals) < window:
            return None
        rsi_window = rsi_vals[-window:]

        price_lows, price_highs = self._find_pivots(window_closes, order=2)

        # Bullish divergence check: Price lower low, RSI higher low
        if len(price_lows) >= 2:
            idx1, p_val1 = price_lows[-2]
            idx2, p_val2 = price_lows[-1]
            # Ensure the second pivot is sufficiently recent and distinctly lower
            if (window - 1 - idx2) <= 3 and p_val2 < p_val1 * 0.998:
                r_val1 = rsi_window[idx1]
                r_val2 = rsi_window[idx2]
                if r_val2 > (r_val1 + 1.0) and r_val2 < 42.0:
                    self.last_trade_bar = ctx.bar_index
                    self.bars_in_position = 0
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=520.0,
                        metadata={
                            "reason": "bullish_rsi_div_confirmed",
                            "price_prev_low": p_val1,
                            "price_curr_low": p_val2,
                            "rsi_prev": r_val1,
                            "rsi_curr": r_val2,
                            "regime": regime
                        }
                    )

        # Bearish divergence check: Price higher high, RSI lower high
        if len(price_highs) >= 2:
            idx1, p_val1 = price_highs[-2]
            idx2, p_val2 = price_highs[-1]
            if (window - 1 - idx2) <= 3 and p_val2 > p_val1 * 1.002:
                r_val1 = rsi_window[idx1]
                r_val2 = rsi_window[idx2]
                if r_val2 < (r_val1 - 1.0) and r_val2 > 58.0:
                    self.last_trade_bar = ctx.bar_index
                    self.bars_in_position = 0
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=520.0,
                        metadata={
                            "reason": "bearish_rsi_div_confirmed",
                            "price_prev_high": p_val1,
                            "price_curr_high": p_val2,
                            "rsi_prev": r_val1,
                            "rsi_curr": r_val2,
                            "regime": regime
                        }
                    )

        return None