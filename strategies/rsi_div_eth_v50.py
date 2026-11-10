from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergencePivot(Strategy):
    METADATA = {
        "name": "EthRsiDivergencePivot",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -100

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

        rsi_vals = [None] * period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_vals.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_vals

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 2:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback + 2:
            return None

        curr_rsi = rsi_series[-1]
        curr_price = ctx.bar.close

        # Position exit management on mean reversion / normalization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_rsi >= 58.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long_exit", "rsi": curr_rsi})
            if pos_dir == "short" and curr_rsi <= 42.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short_exit", "rsi": curr_rsi})
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate: avoid extreme crisis melt
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Pivot detection over window [-lookback : -1]
        eval_closes = closes[-self.lookback:]
        eval_rsi = rsi_series[-self.lookback:]

        pivots_low = []
        pivots_high = []

        # Find 3-bar swing pivots
        for i in range(1, len(eval_closes) - 1):
            if eval_closes[i] < eval_closes[i - 1] and eval_closes[i] <= eval_closes[i + 1]:
                pivots_low.append((i, eval_closes[i], eval_rsi[i]))
            if eval_closes[i] > eval_closes[i - 1] and eval_closes[i] >= eval_closes[i + 1]:
                pivots_high.append((i, eval_closes[i], eval_rsi[i]))

        # Bullish Divergence Event:
        # Latest bar formed a confirmed swing low or recent hook while RSI oversold/divergent
        if len(pivots_low) >= 2:
            prev_idx, prev_price, prev_rsi = pivots_low[-2]
            curr_idx, c_price, c_rsi = pivots_low[-1]
            
            # Must be a fresh pivot confirmed within the last 3 bars
            if (len(eval_closes) - 1 - curr_idx) <= 3:
                # Price Lower Low, RSI Higher Low
                if c_price < prev_price and c_rsi > (prev_rsi + 1.5) and c_rsi < 45.0:
                    conf = min(0.9, 0.55 + max(0.0, (40.0 - c_rsi) * 0.015))
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_div_pivot",
                            "rsi": curr_rsi,
                            "c_rsi": c_rsi,
                            "prev_rsi": prev_rsi,
                            "c_price": c_price,
                            "prev_price": prev_price
                        }
                    )

        # Bearish Divergence Event:
        if len(pivots_high) >= 2:
            prev_idx, prev_price, prev_rsi = pivots_high[-2]
            curr_idx, c_price, c_rsi = pivots_high[-1]

            # Must be a fresh pivot confirmed within the last 3 bars
            if (len(eval_closes) - 1 - curr_idx) <= 3:
                # Price Higher High, RSI Lower High
                if c_price > prev_price and c_rsi < (prev_rsi - 1.5) and c_rsi > 55.0:
                    conf = min(0.9, 0.55 + max(0.0, (c_rsi - 60.0) * 0.015))
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_div_pivot",
                            "rsi": curr_rsi,
                            "c_rsi": c_rsi,
                            "prev_rsi": prev_rsi,
                            "c_price": c_price,
                            "prev_price": prev_price
                        }
                    )

        return None