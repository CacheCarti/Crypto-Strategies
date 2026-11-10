from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSelective(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSelective",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.window = 35
        self.cooldown_bars = 8
        self.last_exit_bar = -100

    def _calculate_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.window + self.rsi_period + 5)
        lows = ctx.lows(self.window + self.rsi_period + 5)
        highs = ctx.highs(self.window + self.rsi_period + 5)

        if len(closes) < self.window + self.rsi_period:
            return None

        rsi_series = self._calculate_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.window:
            return None

        current_rsi = rsi_series[-1]
        current_close = closes[-1]

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion normalization exit
            if pos_dir == "long" and current_rsi >= 58.0:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "rsi_normalized_long_exit",
                    "rsi": current_rsi,
                    "price": current_close
                })
            elif pos_dir == "short" and current_rsi <= 42.0:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "rsi_normalized_short_exit",
                    "rsi": current_rsi,
                    "price": current_close
                })
            return None

        # Mandatory cooldown to prevent overtrading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety: avoid entering against severe market crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        sub_lows = lows[-self.window:]
        sub_highs = highs[-self.window:]
        sub_rsi = rsi_series[-self.window:]

        # Find swing low pivot (Bullish Divergence)
        # We search for prior trough in the window excluding the most recent 4 bars
        search_range = range(3, self.window - 4)
        bullish_div = False
        prior_rsi_low = 0.0
        prior_price_low = 0.0

        if current_rsi < 38.0:
            for i in search_range:
                # Local pivot low in price and rsi
                if sub_lows[i] <= sub_lows[i - 1] and sub_lows[i] <= sub_lows[i + 1] and \
                   sub_lows[i] <= sub_lows[i - 2] and sub_lows[i] <= sub_lows[i + 2]:
                    # Pivot RSI was deeply oversold, current price broke pivot low, but current RSI is distinctly higher
                    if sub_rsi[i] < 34.0 and sub_lows[-1] < sub_lows[i] and current_rsi > (sub_rsi[i] + 3.5):
                        bullish_div = True
                        prior_rsi_low = sub_rsi[i]
                        prior_price_low = sub_lows[i]
                        break

        if bullish_div:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "current_rsi": current_rsi,
                    "prior_rsi": prior_rsi_low,
                    "current_price": current_close,
                    "prior_price_low": prior_price_low
                }
            )

        # Find swing high pivot (Bearish Divergence)
        bearish_div = False
        prior_rsi_high = 0.0
        prior_price_high = 0.0

        if current_rsi > 62.0:
            for i in search_range:
                # Local pivot high in price and rsi
                if sub_highs[i] >= sub_highs[i - 1] and sub_highs[i] >= sub_highs[i + 1] and \
                   sub_highs[i] >= sub_highs[i - 2] and sub_highs[i] >= sub_highs[i + 2]:
                    # Pivot RSI was overbought, current price broke pivot high, but current RSI is distinctly lower
                    if sub_rsi[i] > 66.0 and sub_highs[-1] > sub_highs[i] and current_rsi < (sub_rsi[i] - 3.5):
                        bearish_div = True
                        prior_rsi_high = sub_rsi[i]
                        prior_price_high = sub_highs[i]
                        break

        if bearish_div:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "current_rsi": current_rsi,
                    "prior_rsi": prior_rsi_high,
                    "current_price": current_close,
                    "prior_price_high": prior_price_high
                }
            )

        return None