from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 3
        self.cooldown_bars = 8
        self.last_exit_bar = -100

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(needed)
        lows = ctx.lows(needed)
        highs = ctx.highs(needed)

        if len(closes) < needed:
            return None

        # Build RSI series over the lookback window
        rsi_series = []
        for i in range(self.lookback + self.pivot_window + 1):
            sub_end = len(closes) - (self.lookback + self.pivot_window - i)
            sub_closes = closes[:sub_end]
            val = self._calc_rsi(sub_closes, self.rsi_period)
            if val is not None:
                rsi_series.append(val)

        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]

        # Manage existing positions: exit when normalized
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_rsi >= 58.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_long", "rsi": current_rsi})
            elif pos_dir == "short" and current_rsi <= 42.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_short", "rsi": current_rsi})
            return None

        # Enforce post-trade cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis conditions
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.6:
            return None

        # Detect swing lows & swing highs
        w = self.pivot_window
        eval_lows = lows[-self.lookback:]
        eval_highs = highs[-self.lookback:]
        eval_rsis = rsi_series[-self.lookback:]

        low_pivots = []
        high_pivots = []
        for i in range(w, len(eval_lows) - w):
            if eval_lows[i] == min(eval_lows[i - w:i + w + 1]):
                low_pivots.append((i, eval_lows[i], eval_rsis[i]))
            if eval_highs[i] == max(eval_highs[i - w:i + w + 1]):
                high_pivots.append((i, eval_highs[i], eval_rsis[i]))

        # Bullish Divergence: Price lower low, RSI higher low, trigger in oversold territory
        if len(low_pivots) >= 2:
            prev_low, curr_low = low_pivots[-2], low_pivots[-1]
            bars_since_pivot = (len(eval_lows) - 1) - curr_low[0]
            if bars_since_pivot <= 3:
                price_ll = curr_low[1] < prev_low[1] * 0.998
                rsi_hl = curr_low[2] > prev_low[2] + 1.5
                if price_ll and rsi_hl and curr_low[2] < 42.0:
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "pivot_rsi_prev": prev_low[2],
                            "pivot_rsi_curr": curr_low[2],
                            "pivot_price_prev": prev_low[1],
                            "pivot_price_curr": curr_low[1],
                        }
                    )

        # Bearish Divergence: Price higher high, RSI lower high, trigger in overbought territory
        if len(high_pivots) >= 2:
            prev_high, curr_high = high_pivots[-2], high_pivots[-1]
            bars_since_pivot = (len(eval_highs) - 1) - curr_high[0]
            if bars_since_pivot <= 3:
                price_hh = curr_high[1] > prev_high[1] * 1.002
                rsi_lh = curr_high[2] < prev_high[2] - 1.5
                if price_hh and rsi_lh and curr_high[2] > 58.0:
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "pivot_rsi_prev": prev_high[2],
                            "pivot_rsi_curr": curr_high[2],
                            "pivot_price_prev": prev_high[1],
                            "pivot_price_curr": curr_high[1],
                        }
                    )

        return None