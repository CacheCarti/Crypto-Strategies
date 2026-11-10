from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 10
        self.last_exit_bar = -999

    def _rsi_series(self, closes, period=14):
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_vals = []
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        if avg_l == 0:
            rsi_vals.append(100.0)
        else:
            rsi_vals.append(100.0 - (100.0 / (1.0 + (avg_g / avg_l))))

        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            if avg_l == 0:
                rsi_vals.append(100.0)
            else:
                rsi_vals.append(100.0 - (100.0 / (1.0 + (avg_g / avg_l))))
        return rsi_vals

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period:
            return None

        # Exit logic if in position: exit when RSI normalizes
        rsi_series = self._rsi_series(closes, self.rsi_period)
        if not rsi_series or len(rsi_series) < self.lookback:
            return None

        curr_rsi = rsi_series[-1]

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_rsi >= 55.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_long", "rsi": curr_rsi})
            elif pos_dir == "short" and curr_rsi <= 45.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_short", "rsi": curr_rsi})
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Alignment for divergence lookback (aligned to closes slice)
        # rsi_series[-1] corresponds to closes[-1]
        c_slice = closes[-self.lookback:]
        r_slice = rsi_series[-self.lookback:]

        # Find local pivot lows/highs in lookback window (excluding current bar)
        # Bullish divergence:
        # Prior pivot low exists: c_slice[i] is local low with RSI oversold (<35)
        # Current bar low is lower than prior pivot low, but current RSI is higher than prior pivot RSI
        bullish_div = False
        bearish_div = False
        prior_p_low = None
        prior_r_low = None

        for i in range(2, len(c_slice) - 3):
            if c_slice[i] < c_slice[i - 1] and c_slice[i] < c_slice[i - 2] and c_slice[i] < c_slice[i + 1] and c_slice[i] < c_slice[i + 2]:
                if r_slice[i] < 35.0:
                    prior_p_low = c_slice[i]
                    prior_r_low = r_slice[i]

        if prior_p_low is not None:
            if c_slice[-1] < prior_p_low and curr_rsi > prior_r_low + 2.0 and curr_rsi < 40.0:
                bullish_div = True

        # Bearish divergence:
        # Prior pivot high exists: c_slice[i] is local high with RSI overbought (>65)
        # Current bar high is higher than prior pivot high, but current RSI is lower than prior pivot RSI
        prior_p_high = None
        prior_r_high = None

        for i in range(2, len(c_slice) - 3):
            if c_slice[i] > c_slice[i - 1] and c_slice[i] > c_slice[i - 2] and c_slice[i] > c_slice[i + 1] and c_slice[i] > c_slice[i + 2]:
                if r_slice[i] > 65.0:
                    prior_p_high = c_slice[i]
                    prior_r_high = r_slice[i]

        if prior_p_high is not None:
            if c_slice[-1] > prior_p_high and curr_rsi < prior_r_high - 2.0 and curr_rsi > 60.0:
                bearish_div = True

        if bullish_div:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "rsi": round(curr_rsi, 2),
                    "prior_pivot_rsi": round(prior_r_low, 2) if prior_r_low else 0.0,
                    "price": round(c_slice[-1], 2),
                    "prior_pivot_price": round(prior_p_low, 2) if prior_p_low else 0.0,
                }
            )

        if bearish_div:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "rsi": round(curr_rsi, 2),
                    "prior_pivot_rsi": round(prior_r_high, 2) if prior_r_high else 0.0,
                    "price": round(c_slice[-1], 2),
                    "prior_pivot_price": round(prior_p_high, 2) if prior_p_high else 0.0,
                }
            )

        return None