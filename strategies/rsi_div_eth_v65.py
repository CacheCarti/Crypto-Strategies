from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceQuality(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceQuality",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # 6 hours target horizon
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.pivot_window = 2
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        rsis = []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
            if i >= period:
                avg_gain = sum(gains[-period:]) / period
                avg_loss = sum(losses[-period:]) / period
                if avg_loss == 0.0:
                    rsi = 100.0
                else:
                    rs = avg_gain / avg_loss
                    rsi = 100.0 - (100.0 / (1.0 + rs))
                rsis.append(rsi)
        return rsis

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 5:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        curr_rsi = rsi_series[-1]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Position management: exit on normalization or max hold
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            if pos_dir == "long":
                if curr_rsi >= 58.0 or bars_held >= 12:
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "rsi_normalized_or_time_stop_long",
                        "rsi": round(curr_rsi, 2),
                        "bars_held": bars_held
                    })
            elif pos_dir == "short":
                if curr_rsi <= 42.0 or bars_held >= 12:
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "rsi_normalized_or_time_stop_short",
                        "rsi": round(curr_rsi, 2),
                        "bars_held": bars_held
                    })
            return None

        # Mandatory cooldown check
        if bars_since_exit < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering against severe crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Identify local pivots over lookback window
        window_closes = closes[-self.lookback:]
        window_rsi = rsi_series[-self.lookback:]

        low_pivots = []
        high_pivots = []
        pw = self.pivot_window

        for i in range(pw, len(window_closes) - pw):
            # Pivot Low
            if all(window_closes[i] <= window_closes[i - j] for j in range(1, pw + 1)) and \
               all(window_closes[i] <= window_closes[i + j] for j in range(1, pw + 1)):
                low_pivots.append((i, window_closes[i], window_rsi[i]))

            # Pivot High
            if all(window_closes[i] >= window_closes[i - j] for j in range(1, pw + 1)) and \
               all(window_closes[i] >= window_closes[i + j] for j in range(1, pw + 1)):
                high_pivots.append((i, window_closes[i], window_rsi[i]))

        # High-conviction Bullish Divergence check
        if len(low_pivots) >= 2:
            p1 = low_pivots[-2]
            p2 = low_pivots[-1]
            idx_diff = p2[0] - p1[0]
            recency = (len(window_closes) - 1) - p2[0]

            # p2 is recent (within 4 bars), p1 happened 5 to 22 bars prior
            if recency <= 4 and 5 <= idx_diff <= 22:
                price_drop_pct = (p2[1] - p1[1]) / p1[1]
                rsi_diff = p2[2] - p1[2]

                # Price makes clear lower low (-0.4% or more), RSI makes clear higher low (+2.5 pts)
                # and recent pivot RSI must be in oversold/rebound territory (<= 38)
                if price_drop_pct <= -0.004 and rsi_diff >= 2.5 and p2[2] <= 38.0:
                    if curr_rsi > p2[2] and curr_rsi < 48.0:  # Confirmed initial turn upward
                        self.entry_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": round(curr_rsi, 2),
                            "p1_rsi": round(p1[2], 2),
                            "p2_rsi": round(p2[2], 2),
                            "price_drop_pct": round(price_drop_pct * 100, 2)
                        })

        # High-conviction Bearish Divergence check
        if len(high_pivots) >= 2:
            p1 = high_pivots[-2]
            p2 = high_pivots[-1]
            idx_diff = p2[0] - p1[0]
            recency = (len(window_closes) - 1) - p2[0]

            if recency <= 4 and 5 <= idx_diff <= 22:
                price_rise_pct = (p2[1] - p1[1]) / p1[1]
                rsi_diff = p2[2] - p1[2]

                # Price makes higher high (+0.4% or more), RSI makes lower high (-2.5 pts)
                # and recent pivot RSI must be in overbought/exhaustion territory (>= 62)
                if price_rise_pct >= 0.004 and rsi_diff <= -2.5 and p2[2] >= 62.0:
                    if curr_rsi < p2[2] and curr_rsi > 52.0:  # Confirmed initial turn downward
                        self.entry_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": round(curr_rsi, 2),
                            "p1_rsi": round(p1[2], 2),
                            "p2_rsi": round(p2[2], 2),
                            "price_rise_pct": round(price_rise_pct * 100, 2)
                        })

        return None