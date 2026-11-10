from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.min_width_bps = 40.0
        self.max_width_bps = 140.0
        self.cooldown_bars = 10
        self.last_trade_bar = -999

    def _rsi(self, closes, period=7):
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 8)
        if len(closes) < self.period + 8:
            return None

        highs = ctx.highs(self.period)
        lows = ctx.lows(self.period)

        ch_high = max(highs)
        ch_low = min(lows)
        midline = (ch_high + ch_low) / 2.0
        width = ch_high - ch_low

        if midline <= 0:
            return None

        width_bps = (width / midline) * 10000.0

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            close_price = ctx.bar.close
            if pos_dir == "long" and close_price >= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_reached_midline",
                    "price": close_price,
                    "midline": midline,
                    "width_bps": round(width_bps, 1)
                })
            elif pos_dir == "short" and close_price <= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_reached_midline",
                    "price": close_price,
                    "midline": midline,
                    "width_bps": round(width_bps, 1)
                })
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if not (self.min_width_bps <= width_bps <= self.max_width_bps):
            return None

        rsi = self._rsi(closes, period=7)
        if rsi is None:
            return None

        close = ctx.bar.close
        low = ctx.bar.low
        high = ctx.bar.high

        # Event: Touch or pierce channel low then close above it with oversold turn
        long_event = (low <= ch_low + (width * 0.05)) and (close > ch_low) and (rsi < 36.0)
        
        # Event: Touch or pierce channel high then close below it with overbought turn
        short_event = (high >= ch_high - (width * 0.05)) and (close < ch_high) and (rsi > 64.0)

        if long_event:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "channel_low_fade_oversold",
                    "width_bps": round(width_bps, 1),
                    "rsi": round(rsi, 2),
                    "ch_low": round(ch_low, 3),
                    "midline": round(midline, 3),
                    "price": round(close, 3)
                }
            )

        if short_event:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "channel_high_fade_overbought",
                    "width_bps": round(width_bps, 1),
                    "rsi": round(rsi, 2),
                    "ch_high": round(ch_high, 3),
                    "midline": round(midline, 3),
                    "price": round(close, 3)
                }
            )

        return None