from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 18000,  # ~5 hours target
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.trend_period = 30
        self.vol_mult = 2.2
        self.range_mult = 1.75
        self.max_hold_bars = 6
        self.cooldown_bars = 5
        self.entry_bar_index = -999
        self.exit_bar_index = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 5)
        highs = ctx.highs(self.trend_period + 5)
        lows = ctx.lows(self.trend_period + 5)
        volumes = ctx.volumes(self.trend_period + 5)

        if len(closes) < self.trend_period:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_index
            pos_dir = ctx.position_direction()
            curr_c = ctx.bar.close
            prev_c = closes[-2]

            # Exit on time horizon exhaustion
            if bars_held >= self.max_hold_bars:
                self.exit_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_exit_reached",
                    "bars_held": bars_held,
                    "close": curr_c
                })

            # Exit on momentum stalling/reversal
            if pos_dir == "long" and curr_c < prev_c and bars_held >= 2:
                self.exit_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_momentum_stall",
                    "bars_held": bars_held,
                    "close": curr_c
                })
            elif pos_dir == "short" and curr_c > prev_c and bars_held >= 2:
                self.exit_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_momentum_stall",
                    "bars_held": bars_held,
                    "close": curr_c
                })

            return None

        # Check cooldown
        if (ctx.bar_index - self.exit_bar_index) < self.cooldown_bars:
            return None

        # Skip high crisis/meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Indicators
        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr_val = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        sma_trend = self._sma(closes, self.trend_period)

        if avg_vol is None or atr_val is None or sma_trend is None or avg_vol <= 0 or atr_val <= 0:
            return None

        curr_bar = ctx.bar
        curr_range = curr_bar.high - curr_bar.low
        body = abs(curr_bar.close - curr_bar.open)
        vol_ratio = curr_bar.volume / avg_vol
        range_ratio = curr_range / atr_val

        # Setup criteria: heavy volume surge + unusually wide range + directional body
        is_vol_burst = vol_ratio >= self.vol_mult
        is_wide_range = range_ratio >= self.range_mult
        is_decisive = body >= (0.5 * curr_range)  # avoid dojis/churn

        if not (is_vol_burst and is_wide_range and is_decisive):
            return None

        # Long burst: strong close near high, above baseline trend
        if curr_bar.close > curr_bar.open and curr_bar.close > sma_trend:
            close_to_high = (curr_bar.high - curr_bar.close) / curr_range if curr_range > 0 else 1.0
            if close_to_high < 0.25:  # closed in top quartile
                self.entry_bar_index = ctx.bar_index
                conf = min(0.9, 0.55 + 0.1 * min(vol_ratio - 2.0, 3.0))
                return ctx.signal("long", confidence=conf, stop_loss_bps=self.METADATA["declared_sl_bps"],
                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                  metadata={
                                      "reason": "vol_burst_expansion_bullish",
                                      "vol_ratio": round(vol_ratio, 2),
                                      "range_ratio": round(range_ratio, 2),
                                      "close": curr_bar.close,
                                      "sma_trend": round(sma_trend, 2)
                                  })

        # Short burst: strong close near low, below baseline trend
        if curr_bar.close < curr_bar.open and curr_bar.close < sma_trend:
            close_to_low = (curr_bar.close - curr_bar.low) / curr_range if curr_range > 0 else 1.0
            if close_to_low < 0.25:  # closed in bottom quartile
                self.entry_bar_index = ctx.bar_index
                conf = min(0.9, 0.55 + 0.1 * min(vol_ratio - 2.0, 3.0))
                return ctx.signal("short", confidence=conf, stop_loss_bps=self.METADATA["declared_sl_bps"],
                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                  metadata={
                                      "reason": "vol_burst_expansion_bearish",
                                      "vol_ratio": round(vol_ratio, 2),
                                      "range_ratio": round(range_ratio, 2),
                                      "close": curr_bar.close,
                                      "sma_trend": round(sma_trend, 2)
                                  })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.exit_bar_index = ctx.bar_index