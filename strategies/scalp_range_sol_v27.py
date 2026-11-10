from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolAdaptiveRangeFade(Strategy):
    METADATA = {
        "name": "SolAdaptiveRangeFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.atr_period = 14
        self.width_lookback = 60
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=10):
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
        needed = max(self.channel_period + self.width_lookback, 70)
        closes = ctx.closes(needed)
        if len(closes) < needed:
            return None

        highs = ctx.highs(needed)
        lows = ctx.lows(needed)
        current_close = ctx.bar.close

        # Compute dynamic channel
        recent_highs = highs[-self.channel_period:]
        recent_lows = lows[-self.channel_period:]
        hh = max(recent_highs)
        ll = min(recent_lows)
        channel_width = hh - ll

        if current_close <= 0 or channel_width <= 0:
            return None

        channel_width_pct = channel_width / current_close
        midline = (hh + ll) / 2.0

        # Dynamic compression filter: channel width relative to recent history
        hist_widths = []
        for i in range(self.channel_period, len(closes) + 1):
            h_win = highs[i - self.channel_period:i]
            l_win = lows[i - self.channel_period:i]
            c_val = closes[i - 1]
            if c_val > 0:
                hist_widths.append((max(h_win) - min(l_win)) / c_val)

        if len(hist_widths) < 20:
            return None

        # Percentile rank of current width
        rank = sum(1 for w in hist_widths if w < channel_width_pct) / len(hist_widths)
        is_compressed = rank <= 0.35

        # In-position management: exit cleanly at channel midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_midline_reached",
                    "close": current_close,
                    "midline": midline
                })
            elif direction == "short" and current_close <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_midline_reached",
                    "close": current_close,
                    "midline": midline
                })
            return None

        # Stand down if in cooldown or market in macro crisis
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Stand down if channel is expanding (breakout/trend mode)
        if not is_compressed:
            return None

        # Secondary momentum check to avoid fading clean breakdown/breakouts
        rsi_val = self._rsi(closes, period=10)
        if rsi_val is None:
            return None

        edge_buffer = channel_width * 0.12

        # Fade lower edge (Long)
        if current_close <= (ll + edge_buffer) and rsi_val < 38.0:
            return ctx.signal("long", confidence=0.68, metadata={
                "reason": "fade_channel_low",
                "width_rank": rank,
                "rsi": rsi_val,
                "channel_low": ll,
                "midline": midline,
                "close": current_close
            })

        # Fade upper edge (Short)
        if current_close >= (hh - edge_buffer) and rsi_val > 62.0:
            return ctx.signal("short", confidence=0.68, metadata={
                "reason": "fade_channel_high",
                "width_rank": rank,
                "rsi": rsi_val,
                "channel_high": hh,
                "midline": midline,
                "close": current_close
            })

        return None