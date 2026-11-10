from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceEvent(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 16

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        # Initial average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsi_series = []
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        lows = ctx.lows(50)
        highs = ctx.highs(50)

        if len(closes) < 45:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
            curr_rsi = rsi_vals[-1] if rsi_vals else 50.0

            # Exit criteria: RSI normalization or time-based horizon
            if pos_dir == "long":
                if curr_rsi >= 58.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_rsi_normalized_or_time_stop",
                        "rsi": curr_rsi,
                        "bars_held": bars_held
                    })
            elif pos_dir == "short":
                if curr_rsi <= 42.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_rsi_normalized_or_time_stop",
                        "rsi": curr_rsi,
                        "bars_held": bars_held
                    })
            return None

        # Hard cooldown check after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety: avoid entering against meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate RSI series aligned with prices
        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        # Slice relevant window (last 30 bars)
        p_lows = lows[-self.lookback:]
        p_highs = highs[-self.lookback:]
        r_vals = rsi_series[-self.lookback:]

        # Find 3-bar local swing points within the window (excluding the active bar)
        troughs = []
        peaks = []
        for i in range(2, len(r_vals) - 1):
            # Pivot Low
            if p_lows[i] <= p_lows[i - 1] and p_lows[i] <= p_lows[i - 2] and p_lows[i] <= p_lows[i + 1]:
                troughs.append((i, p_lows[i], r_vals[i]))
            # Pivot High
            if p_highs[i] >= p_highs[i - 1] and p_highs[i] >= p_highs[i - 2] and p_highs[i] >= p_highs[i + 1]:
                peaks.append((i, p_highs[i], r_vals[i]))

        curr_close = closes[-1]
        curr_rsi = rsi_series[-1]

        # Regular Bullish Divergence:
        # Prior pivot low was deep oversold, recent pivot low has lower price but higher RSI
        if len(troughs) >= 2:
            t1, t2 = troughs[-2], troughs[-1]  # t1 is older, t2 is more recent
            # Event boundary: t2 must be relatively fresh (within last 6 bars)
            if (len(r_vals) - 1 - t2[0]) <= 6:
                price_lower = t2[1] < t1[1]
                rsi_higher = t2[2] > t1[2]
                if price_lower and rsi_higher and t1[2] <= 38.0 and curr_rsi < 48.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.8,
                        stop_loss_bps=250.0,
                        take_profit_bps=500.0,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi_prior": t1[2],
                            "rsi_recent": t2[2],
                            "price_prior": t1[1],
                            "price_recent": t2[1],
                            "curr_rsi": curr_rsi
                        }
                    )

        # Regular Bearish Divergence:
        # Prior pivot high was overbought, recent pivot high has higher price but lower RSI
        if len(peaks) >= 2:
            p1, p2 = peaks[-2], peaks[-1]  # p1 is older, p2 is more recent
            if (len(r_vals) - 1 - p2[0]) <= 6:
                price_higher = p2[1] > p1[1]
                rsi_lower = p2[2] < p1[2]
                if price_higher and rsi_lower and p1[2] >= 62.0 and curr_rsi > 52.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        stop_loss_bps=250.0,
                        take_profit_bps=500.0,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi_prior": p1[2],
                            "rsi_recent": p2[2],
                            "price_prior": p1[1],
                            "price_recent": p2[1],
                            "curr_rsi": curr_rsi
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index