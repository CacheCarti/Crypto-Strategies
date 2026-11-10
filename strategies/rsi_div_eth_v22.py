from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 18

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        rsi_series = []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
            
            if i >= period:
                avg_gain = sum(gains[-period:]) / period
                avg_loss = sum(losses[-period:]) / period
                if avg_loss == 0.0:
                    rsi_series.append(100.0)
                else:
                    rs = avg_gain / avg_loss
                    rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(needed)
        lows = ctx.lows(needed)
        highs = ctx.highs(needed)

        if len(closes) < needed:
            return None

        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback + 2:
            return None

        current_rsi = rsi_vals[-1]
        regime = ctx.regime  # "calm", "normal", "volatile", "crisis"

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Normalization or time-stop exit
            should_exit = False
            exit_reason = ""
            if pos_dir == "long":
                if current_rsi >= 55.0:
                    should_exit = True
                    exit_reason = "rsi_normalized_long"
                elif bars_held >= self.max_hold_bars:
                    should_exit = True
                    exit_reason = "time_stop_long"
            elif pos_dir == "short":
                if current_rsi <= 45.0:
                    should_exit = True
                    exit_reason = "rsi_normalized_short"
                elif bars_held >= self.max_hold_bars:
                    should_exit = True
                    exit_reason = "time_stop_short"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": exit_reason, "rsi": round(current_rsi, 2), "bars_held": bars_held}
                )
            return None

        # Mandatory Cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate based on prior evidence: avoid extreme/unstable volatility
        if regime in ("volatile", "crisis"):
            return None

        # Divergence Detection over the last `lookback` bars
        # Focus on two pivot lows (for bullish div) or two pivot highs (for bearish div)
        # We divide the window into prior window [-28 : -8] and recent window [-7 : -1]
        prior_lows = lows[-self.lookback:-7]
        recent_lows = lows[-7:]
        prior_rsi = rsi_vals[-self.lookback:-7]
        recent_rsi = rsi_vals[-7:]

        min_prior_low = min(prior_lows)
        min_recent_low = min(recent_lows)
        idx_prior_low = prior_lows.index(min_prior_low)
        idx_recent_low = recent_lows.index(min_recent_low)

        rsi_at_prior_low = prior_rsi[idx_prior_low]
        rsi_at_recent_low = recent_rsi[idx_recent_low]

        # Bullish Divergence: Price Lower Low, RSI Higher Low, and RSI oversold/rebounding
        bull_div = (
            min_recent_low < min_prior_low and
            rsi_at_recent_low > rsi_at_prior_low + 2.0 and
            rsi_at_prior_low < 35.0 and
            current_rsi > rsi_at_recent_low and
            current_rsi < 48.0
        )

        if bull_div:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "current_rsi": round(current_rsi, 2),
                    "rsi_prior": round(rsi_at_prior_low, 2),
                    "rsi_recent": round(rsi_at_recent_low, 2),
                    "price_prior_low": round(min_prior_low, 2),
                    "price_recent_low": round(min_recent_low, 2),
                    "regime": regime
                }
            )

        # Bearish Divergence
        prior_highs = highs[-self.lookback:-7]
        recent_highs = highs[-7:]

        max_prior_high = max(prior_highs)
        max_recent_high = max(recent_highs)
        idx_prior_high = prior_highs.index(max_prior_high)
        idx_recent_high = recent_highs.index(max_recent_high)

        rsi_at_prior_high = prior_rsi[idx_prior_high]
        rsi_at_recent_high = recent_rsi[idx_recent_high]

        bear_div = (
            max_recent_high > max_prior_high and
            rsi_at_recent_high < rsi_at_prior_high - 2.0 and
            rsi_at_prior_high > 65.0 and
            current_rsi < rsi_at_recent_high and
            current_rsi > 52.0
        )

        if bear_div:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "current_rsi": round(current_rsi, 2),
                    "rsi_prior": round(rsi_at_prior_high, 2),
                    "rsi_recent": round(rsi_at_recent_high, 2),
                    "price_prior_high": round(max_prior_high, 2),
                    "price_recent_high": round(max_recent_high, 2),
                    "regime": regime
                }
            )

        return None