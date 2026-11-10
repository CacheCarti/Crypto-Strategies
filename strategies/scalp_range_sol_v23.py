from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 45
        self.last_exit_bar = -999
        self.last_entry_bar = -999

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
        n = self.channel_period + 5
        closes = ctx.closes(n)
        highs = ctx.highs(n)
        lows = ctx.lows(n)

        if len(closes) < n:
            return None

        # Standing down in high stress / trending macro states
        macro_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if macro_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        recent_highs = highs[-self.channel_period:]
        recent_lows = lows[-self.channel_period:]
        channel_high = max(recent_highs)
        channel_low = min(recent_lows)
        channel_mid = (channel_high + channel_low) / 2.0
        price = ctx.bar.close

        if price <= 0:
            return None

        channel_width_bps = ((channel_high - channel_low) / price) * 10000.0

        # Position management: fade to midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and price >= channel_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midline_reached_long",
                    "price": price,
                    "channel_mid": channel_mid,
                    "channel_width_bps": channel_width_bps
                })
            elif pos_dir == "short" and price <= channel_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midline_reached_short",
                    "price": price,
                    "channel_mid": channel_mid,
                    "channel_width_bps": channel_width_bps
                })
            return None

        # Mandatory cooldown after exits and entries to keep trade counts in check (10-120 target)
        if (ctx.bar_index - self.last_exit_bar < self.cooldown_bars) or \
           (ctx.bar_index - self.last_entry_bar < self.cooldown_bars):
            return None

        # Compression check: channel must be narrow (sideways chop, avoiding trending expansions)
        if channel_width_bps < 80.0 or channel_width_bps > 220.0:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None:
            return None
        atr_bps = (atr / price) * 10000.0
        if atr_bps > 35.0:
            return None

        rsi = self._rsi(closes, period=10)
        if rsi is None:
            return None

        # Edges definitions
        edge_zone = (channel_high - channel_low) * 0.12
        is_near_low = price <= (channel_low + edge_zone)
        is_near_high = price >= (channel_high - edge_zone)

        # Long near support with momentum oversold
        if is_near_low and rsi < 32.0:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_low_oversold",
                    "price": price,
                    "rsi": rsi,
                    "channel_low": channel_low,
                    "channel_width_bps": channel_width_bps
                }
            )

        # Short near resistance with momentum overbought
        if is_near_high and rsi > 68.0:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_high_overbought",
                    "price": price,
                    "rsi": rsi,
                    "channel_high": channel_high,
                    "channel_width_bps": channel_width_bps
                }
            )

        return None