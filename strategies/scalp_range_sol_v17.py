from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowChannelFader(Strategy):
    METADATA = {
        "name": "SolNarrowChannelFader",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 110.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 75,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.vol_lookback = 60
        self.cooldown_bars = 40
        self.last_exit_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = self.channel_period + self.vol_lookback
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)

        recent_highs = highs[-self.channel_period:]
        recent_lows = lows[-self.channel_period:]
        curr_close = closes[-1]

        highest = max(recent_highs)
        lowest = min(recent_lows)
        channel_width = highest - lowest

        if channel_width <= 0:
            return None

        curr_width_bps = (channel_width / curr_close) * 10000.0
        midline = (highest + lowest) / 2.0

        # Dynamic channel width ranking over past lookback
        past_widths_bps = []
        for i in range(self.vol_lookback):
            idx_end = len(closes) - i
            idx_start = idx_end - self.channel_period
            sub_h = highs[idx_start:idx_end]
            sub_l = lows[idx_start:idx_end]
            c = closes[idx_end - 1]
            w = max(sub_h) - min(sub_l)
            if c > 0:
                past_widths_bps.append((w / c) * 10000.0)

        # Percentile rank of current width
        smaller_count = sum(1 for w in past_widths_bps if w < curr_width_bps)
        width_percentile = smaller_count / float(len(past_widths_bps))

        # Position management: exit around midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_tp_midline_reached",
                        "close": curr_close,
                        "midline": midline,
                        "width_bps": curr_width_bps,
                    }
                )
            elif pos_dir == "short" and curr_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_tp_midline_reached",
                        "close": curr_close,
                        "midline": midline,
                        "width_bps": curr_width_bps,
                    }
                )
            return None

        # Mandatory cooldown after exits to restrict trade count
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading ranges in crisis/meltdown
        mkt_regime = str(ctx.market.get("regime", "NORMAL")).upper()
        if mkt_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Condition: Range must be compressed (bottom 25% of width history, with minimum room to trade)
        is_narrow = (width_percentile <= 0.25) and (curr_width_bps >= 50.0)
        if not is_narrow:
            return None

        pos_in_range = (curr_close - lowest) / channel_width

        # Fast RSI (7) to capture turn/fade exhaustion at boundaries
        rsi_len = 7
        gains, losses = 0.0, 0.0
        for i in range(len(closes) - rsi_len, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        if losses == 0:
            rsi7 = 100.0
        else:
            rs = (gains / rsi_len) / (losses / rsi_len)
            rsi7 = 100.0 - (100.0 / (1.0 + rs))

        # Fade near channel low (lower 12% of range) with oversold bounce
        if pos_in_range <= 0.12 and rsi7 <= 32.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=75.0,
                take_profit_bps=110.0,
                horizon_seconds=900,
                metadata={
                    "reason": "range_low_fade",
                    "pos_in_range": pos_in_range,
                    "rsi7": rsi7,
                    "width_bps": curr_width_bps,
                    "width_pctl": width_percentile,
                }
            )

        # Fade near channel high (upper 12% of range) with overbought rejection
        if pos_in_range >= 0.88 and rsi7 >= 68.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=75.0,
                take_profit_bps=110.0,
                horizon_seconds=900,
                metadata={
                    "reason": "range_high_fade",
                    "pos_in_range": pos_in_range,
                    "rsi7": rsi7,
                    "width_bps": curr_width_bps,
                    "width_pctl": width_percentile,
                }
            )

        return None