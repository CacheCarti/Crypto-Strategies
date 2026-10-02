from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalper(Strategy):
    METADATA = {
        "name": "SOL Range Fade Scalper",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 135.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 36
        self.rsi_period = 10
        self.min_width_pct = 0.0065   # 65 bps minimum range
        self.max_width_pct = 0.0140   # 140 bps maximum range (above this is high volatility trend)
        self.edge_threshold = 0.07    # strict: within 7% of 36-bar extreme
        self.cooldown_bars = 48       # mandatory 4-hour cooldown after exit to curb friction
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period=10):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)

        if len(closes) < self.channel_period + 5 or len(highs) < self.channel_period or len(lows) < self.channel_period:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        hh = max(highs)
        ll = min(lows)
        range_span = hh - ll
        midline = (hh + ll) / 2.0

        if midline <= 0 or range_span <= 0:
            return None

        width_pct = range_span / midline
        relative_pos = (current_close - ll) / range_span

        # Active trade management: fade back to midline target
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_take_profit_midline_reached",
                        "price": current_close,
                        "midline": midline,
                        "width_pct": width_pct,
                    }
                )
            elif pos_dir == "short" and current_close <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_take_profit_midline_reached",
                        "price": current_close,
                        "midline": midline,
                        "width_pct": width_pct,
                    }
                )
            return None

        # Hard multi-bar cooldown gating to stay within target 30-80 trade count
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Regime filters: only trade calm/neutral sideways tape
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if market_regime in ["CRISIS", "MELTDOWN", "HIGH_VOL"] or crisis_score > 0.20:
            return None

        if trend_regime not in ["neutral", ""]:
            return None

        # Channel width boundary check: must have enough room for fees but no breakout
        if not (self.min_width_pct <= width_pct <= self.max_width_pct):
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        # Long near channel floor: oversold RSI + bullish rejection candle (close > open)
        if relative_pos <= self.edge_threshold and rsi_val <= 28.0 and current_close > current_open:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=90.0,
                take_profit_bps=135.0,
                horizon_seconds=900,
                metadata={
                    "reason": "range_floor_fade_strict_oversold",
                    "price": current_close,
                    "channel_low": ll,
                    "channel_high": hh,
                    "midline": midline,
                    "relative_pos": relative_pos,
                    "width_pct": width_pct,
                    "rsi": rsi_val,
                }
            )

        # Short near channel ceiling: overbought RSI + bearish rejection candle (close < open)
        if relative_pos >= (1.0 - self.edge_threshold) and rsi_val >= 72.0 and current_close < current_open:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=90.0,
                take_profit_bps=135.0,
                horizon_seconds=900,
                metadata={
                    "reason": "range_ceiling_fade_strict_overbought",
                    "price": current_close,
                    "channel_low": ll,
                    "channel_high": hh,
                    "midline": midline,
                    "relative_pos": relative_pos,
                    "width_pct": width_pct,
                    "rsi": rsi_val,
                }
            )

        return None