from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.max_width_pct = 1.3
        self.min_width_pct = 0.45
        self.edge_threshold = 0.12
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 8

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period:
            return None

        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Dynamic channel parameters over previous bars
        window_highs = highs[-self.channel_period:]
        window_lows = lows[-self.channel_period:]
        highest = max(window_highs)
        lowest = min(window_lows)
        channel_span = highest - lowest
        midline = (highest + lowest) / 2.0

        if midline <= 0:
            return None

        channel_width_pct = (channel_span / midline) * 100.0

        # Position Management & Timed/Midline Exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Midline target exit
            if pos_dir == "long" and current_price >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_midline_reached", "price": current_price, "midline": midline})
            elif pos_dir == "short" and current_price <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_midline_reached", "price": current_price, "midline": midline})

            # Time-based risk control (structurally avoids long chop bleed)
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "max_bars_timeout", "bars_held": bars_held})

            # Stand down / emergency bail if channel expands rapidly into breakout trend
            if channel_width_pct > self.max_width_pct * 1.3:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_breakout_bail", "width_pct": channel_width_pct})

            return None

        # Cooldown guard after any closed trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Macro/market volatility filter: only trade quiet/sideways tape
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Channel width bounds: must be narrow chop, not trending or dead-flat
        if not (self.min_width_pct <= channel_width_pct <= self.max_width_pct):
            return None

        # Relative position within the channel (0.0 = channel low, 1.0 = channel high)
        if channel_span <= 0:
            return None
        rel_pos = (current_price - lowest) / channel_span

        # Fade conditions: structural stop outside channel edge
        # Long near the bottom edge
        if rel_pos <= self.edge_threshold and current_price > lowest:
            # Distance to stop (lowest) in bps
            dist_to_low_bps = ((current_price - lowest) / current_price) * 10000.0
            sl_bps = max(60.0, min(140.0, dist_to_low_bps + 25.0))
            tp_bps = max(70.0, min(180.0, ((midline - current_price) / current_price) * 10000.0))

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "fade_channel_low",
                    "rel_pos": round(rel_pos, 4),
                    "width_pct": round(channel_width_pct, 2),
                    "price": current_price,
                    "channel_low": lowest,
                    "midline": midline
                }
            )

        # Short near the top edge
        if rel_pos >= (1.0 - self.edge_threshold) and current_price < highest:
            dist_to_high_bps = ((highest - current_price) / current_price) * 10000.0
            sl_bps = max(60.0, min(140.0, dist_to_high_bps + 25.0))
            tp_bps = max(70.0, min(180.0, ((current_price - midline) / current_price) * 10000.0))

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "fade_channel_high",
                    "rel_pos": round(rel_pos, 4),
                    "width_pct": round(channel_width_pct, 2),
                    "price": current_price,
                    "channel_high": highest,
                    "midline": midline
                }
            )

        return None