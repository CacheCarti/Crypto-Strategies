from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 120,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.history_len = 120
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.history_len)
        highs = ctx.highs(self.history_len)
        lows = ctx.lows(self.history_len)

        if len(closes) < self.history_len:
            return None

        # Regime safety filter from market engine
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        # 30-bar rolling channel statistics
        curr_highs = highs[-self.channel_period:]
        curr_lows = lows[-self.channel_period:]
        highest = max(curr_highs)
        lowest = min(curr_lows)
        midline = (highest + lowest) / 2.0
        current_close = closes[-1]

        channel_width_pct = (highest - lowest) / midline if midline > 0 else 0.0

        # Calculate historical distribution of channel widths to avoid magic hardcoded thresholds
        historical_widths = []
        for i in range(self.channel_period, self.history_len + 1):
            h_slice = highs[i - self.channel_period : i]
            l_slice = lows[i - self.channel_period : i]
            hi = max(h_slice)
            lo = min(l_slice)
            mid = (hi + lo) / 2.0
            if mid > 0:
                historical_widths.append((hi - lo) / mid)

        if len(historical_widths) < 20:
            return None

        # Sort to compute percentiles
        sorted_widths = sorted(historical_widths)
        p35_idx = int(len(sorted_widths) * 0.35)
        p35_width = sorted_widths[p35_idx]

        # In-position management: exit at midline mean reversion
        if ctx.has_position():
            direction = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", current_close) if ctx.position else current_close

            # Target reached: close at channel midline
            if direction == "long" and current_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "midline_reached_long", "price": current_close, "midline": midline}
                )
            elif direction == "short" and current_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "midline_reached_short", "price": current_close, "midline": midline}
                )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Only fade when the tape is narrow (bottom 35th percentile of recent channel widths)
        if channel_width_pct > p35_width:
            return None

        # Channel position: 0.0 = channel bottom, 1.0 = channel top
        rng_span = highest - lowest
        if rng_span <= 0:
            return None
        rel_pos = (current_close - lowest) / rng_span

        # Fast momentum check to avoid fading a violent breakout in progress
        rsi_val = self._rsi(closes, period=10)

        # Long near channel low (bottom 12% of range) with non-collapsed RSI
        if rel_pos <= 0.12 and rsi_val >= 25.0:
            conf = min(0.9, max(0.5, (0.12 - rel_pos) / 0.12 + 0.5))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "narrow_channel_low_fade",
                    "rel_pos": round(rel_pos, 4),
                    "rsi": round(rsi_val, 2),
                    "channel_width_pct": round(channel_width_pct, 4),
                    "p35_width": round(p35_width, 4)
                }
            )

        # Short near channel high (top 12% of range) with non-euphoric RSI
        if rel_pos >= 0.88 and rsi_val <= 75.0:
            conf = min(0.9, max(0.5, (rel_pos - 0.88) / 0.12 + 0.5))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "narrow_channel_high_fade",
                    "rel_pos": round(rel_pos, 4),
                    "rsi": round(rsi_val, 2),
                    "channel_width_pct": round(channel_width_pct, 4),
                    "p35_width": round(p35_width, 4)
                }
            )

        return None