from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolChopChannelFade(Strategy):
    METADATA = {
        "name": "SOL Chop Channel Fade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 36
        self.rsi_period = 9
        self.min_width_bps = 65.0
        self.max_width_bps = 140.0
        self.cooldown_bars = 36
        self.last_trade_bar = -999

    def _rsi(self, closes: list, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)

        if len(closes) < self.channel_period + 5 or len(highs) < self.channel_period or len(lows) < self.channel_period:
            return None

        current_close = ctx.bar.close
        chan_high = max(highs)
        chan_low = min(lows)
        span = chan_high - chan_low

        if span <= 0:
            return None

        midline = (chan_high + chan_low) / 2.0
        width_bps = (span / midline) * 10000.0

        # Position management: Take profits at channel midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_midline_exit",
                        "close": round(current_close, 3),
                        "midline": round(midline, 3),
                        "width_bps": round(width_bps, 1),
                    },
                )
            elif direction == "short" and current_close <= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_midline_exit",
                        "close": round(current_close, 3),
                        "midline": round(midline, 3),
                        "width_bps": round(width_bps, 1),
                    },
                )
            return None

        # Hard multi-bar cooldown to strictly suppress overtrading
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Stand down in volatile, crisis, or non-neutral trend regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if ctx.regime in ("volatile", "crisis") or market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None
        if trend_regime != "neutral" or crisis_score > 0.20:
            return None

        # Channel width gate: only trade well-defined, profitable consolidation boxes
        if width_bps < self.min_width_bps or width_bps > self.max_width_bps:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        pos_in_range = (current_close - chan_low) / span

        # High-conviction Long: extreme bottom 8% of chop range + deeply oversold RSI
        if pos_in_range <= 0.08 and rsi_val <= 28.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "channel_support_fade_oversold",
                    "rsi": round(rsi_val, 2),
                    "pos_in_range": round(pos_in_range, 3),
                    "chan_low": round(chan_low, 3),
                    "chan_high": round(chan_high, 3),
                    "width_bps": round(width_bps, 1),
                },
            )

        # High-conviction Short: extreme top 8% of chop range + deeply overbought RSI
        if pos_in_range >= 0.92 and rsi_val >= 72.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "channel_resistance_fade_overbought",
                    "rsi": round(rsi_val, 2),
                    "pos_in_range": round(pos_in_range, 3),
                    "chan_low": round(chan_low, 3),
                    "chan_high": round(chan_high, 3),
                    "width_bps": round(width_bps, 1),
                },
            )

        return None