from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_arm = 3
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calc_rsi_series(self, closes, period=14):
        n = len(closes)
        if n < period + 1:
            return []
        gains = [0.0] * n
        losses = [0.0] * n
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains[i] = diff
            else:
                losses[i] = -diff

        avg_gain = sum(gains[1:period + 1]) / period
        avg_loss = sum(losses[1:period + 1]) / period

        rsi_series = [50.0] * period
        first_rsi = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
        rsi_series.append(first_rsi)

        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_val = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_val = 100.0 - (100.0 / (1.0 + rs))
            rsi_series.append(rsi_val)
        return rsi_series

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars = self.lookback + self.pivot_arm + self.rsi_period + 5
        closes = ctx.closes(total_bars)
        highs = ctx.highs(total_bars)
        lows = ctx.lows(total_bars)

        if len(closes) < total_bars:
            return None

        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback + self.pivot_arm + 2:
            return None

        curr_rsi = rsi_vals[-1]
        curr_price = ctx.bar.close

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Normalized RSI or structural mean reversion exit
            if pos_dir == "long":
                if curr_rsi >= 55.0 or bars_held >= 18:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "rsi_reversion_long_exit",
                        "rsi": curr_rsi,
                        "bars_held": bars_held
                    })
            elif pos_dir == "short":
                if curr_rsi <= 45.0 or bars_held >= 18:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "rsi_reversion_short_exit",
                        "rsi": curr_rsi,
                        "bars_held": bars_held
                    })
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Pivot Low & Pivot High identification across window
        k = self.pivot_arm
        low_pivots = []
        high_pivots = []

        start_idx = len(closes) - self.lookback - k
        end_idx = len(closes) - k

        for i in range(start_idx, end_idx):
            # Check Pivot Low
            val_l = lows[i]
            if all(val_l < lows[i - j] for j in range(1, k + 1)) and all(val_l <= lows[i + j] for j in range(1, k + 1)):
                low_pivots.append((i, lows[i], rsi_vals[i]))

            # Check Pivot High
            val_h = highs[i]
            if all(val_h > highs[i - j] for j in range(1, k + 1)) and all(val_h >= highs[i + j] for j in range(1, k + 1)):
                high_pivots.append((i, highs[i], rsi_vals[i]))

        atr = self._atr(highs, lows, closes, 14)
        atr_bps = (atr / curr_price * 10000.0) if (atr and curr_price > 0) else 250.0
        sl_bps = max(200.0, min(500.0, atr_bps * 1.8))
        tp_bps = sl_bps * 1.5

        # Check Bullish Divergence (Higher RSI Low on Lower Price Low)
        if len(low_pivots) >= 2:
            prev_p, curr_p = low_pivots[-2], low_pivots[-1]
            # Must be a recent confirmed pivot
            if (len(closes) - 1 - curr_p[0]) <= (k + 3):
                # Bullish divergence setup + oversold context
                if curr_p[1] < prev_p[1] and curr_p[2] > prev_p[2] and curr_p[2] < 42.0:
                    if curr_rsi > curr_p[2]:  # Momentum starting to turn up
                        self.entry_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75,
                                          stop_loss_bps=sl_bps,
                                          take_profit_bps=tp_bps,
                                          metadata={
                                              "reason": "bullish_rsi_divergence",
                                              "price_p1": prev_p[1],
                                              "price_p2": curr_p[1],
                                              "rsi_p1": prev_p[2],
                                              "rsi_p2": curr_p[2],
                                              "current_rsi": curr_rsi
                                          })

        # Check Bearish Divergence (Lower RSI High on Higher Price High)
        if len(high_pivots) >= 2:
            prev_p, curr_p = high_pivots[-2], high_pivots[-1]
            if (len(closes) - 1 - curr_p[0]) <= (k + 3):
                # Bearish divergence setup + overbought context
                if curr_p[1] > prev_p[1] and curr_p[2] < prev_p[2] and curr_p[2] > 58.0:
                    if curr_rsi < curr_p[2]:  # Momentum turning down
                        self.entry_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75,
                                          stop_loss_bps=sl_bps,
                                          take_profit_bps=tp_bps,
                                          metadata={
                                              "reason": "bearish_rsi_divergence",
                                              "price_p1": prev_p[1],
                                              "price_p2": curr_p[1],
                                              "rsi_p1": prev_p[2],
                                              "rsi_p2": curr_p[2],
                                              "current_rsi": curr_rsi
                                          })

        return None