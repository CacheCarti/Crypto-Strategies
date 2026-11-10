from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        
        # Compute gains and losses
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        # Initial averages
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi_series = []
        if avg_loss == 0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        # Smoothed Wilder's RSI series
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_req = self.lookback + self.rsi_period + 5
        closes = ctx.closes(total_req)
        lows = ctx.lows(total_req)
        highs = ctx.highs(total_req)

        if len(closes) < total_req:
            return None

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            rsi_series = self._calc_rsi_series(closes, self.rsi_period)
            curr_rsi = rsi_series[-1] if rsi_series else 50.0

            # Normalization or max time exit
            if pos_dir == "long" and (curr_rsi >= 58.0 or bars_held >= 14):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_or_time_stop_long", "rsi": curr_rsi, "bars_held": bars_held})
            elif pos_dir == "short" and (curr_rsi <= 42.0 or bars_held >= 14):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_or_time_stop_short", "rsi": curr_rsi, "bars_held": bars_held})
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown states
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        curr_rsi = rsi_series[-1]
        curr_low = lows[-1]
        curr_high = highs[-1]

        # Use recent 15-30 bars window for the prior pivot anchor (excluding last 3 bars for distinctness)
        prior_lows = lows[-(self.lookback): -3]
        prior_highs = highs[-(self.lookback): -3]
        prior_rsi = rsi_series[-(self.lookback): -3]

        min_prior_low = min(prior_lows)
        idx_low = prior_lows.index(min_prior_low)
        rsi_at_min_low = prior_rsi[idx_low]

        max_prior_high = max(prior_highs)
        idx_high = prior_highs.index(max_prior_high)
        rsi_at_max_high = prior_rsi[idx_high]

        # Bullish Divergence: Current price creates new low, but current RSI forms a higher low and begins turning up
        # Conditioned on RSI being in an oversold / recovery state (< 42)
        if (curr_low < min_prior_low) and (curr_rsi > rsi_at_min_low + 2.0) and (curr_rsi < 42.0) and (rsi_series[-1] > rsi_series[-2]):
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "curr_low": curr_low,
                    "min_prior_low": min_prior_low,
                    "curr_rsi": curr_rsi,
                    "rsi_at_min_low": rsi_at_min_low,
                }
            )

        # Bearish Divergence: Current price creates new high, but current RSI forms a lower high and turns down
        # Conditioned on RSI being in an overbought / exhausted state (> 58)
        if (curr_high > max_prior_high) and (curr_rsi < rsi_at_max_high - 2.0) and (curr_rsi > 58.0) and (rsi_series[-1] < rsi_series[-2]):
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "curr_high": curr_high,
                    "max_prior_high": max_prior_high,
                    "curr_rsi": curr_rsi,
                    "rsi_at_max_high": rsi_at_max_high,
                }
            )

        return None