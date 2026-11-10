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
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.rsi_period = 7
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.max_width_pct = 0.014
        self.min_width_pct = 0.003

    def _rsi(self, closes, period):
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
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 2:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Exclude crisis / violent shock regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset volatility check: stand down if BTC is actively trending heavily
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if abs(btc_ret) > 3.0:
            return None

        # Calculate Donchian Channel on past bars (excluding current to prevent lookahead)
        prior_highs = highs[-self.channel_period - 1:-1]
        prior_lows = lows[-self.channel_period - 1:-1]
        hh = max(prior_highs)
        ll = min(prior_lows)
        mid = (hh + ll) / 2.0
        width = hh - ll

        if mid <= 0:
            return None

        width_pct = width / mid

        # Range filter: channel must be narrow (chop), but non-zero
        if width_pct > self.max_width_pct or width_pct < self.min_width_pct:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Midline exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= mid:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "long_midline_reached",
                    "price": curr_close,
                    "midline": mid
                })
            elif pos_dir == "short" and curr_close <= mid:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "short_midline_reached",
                    "price": curr_close,
                    "midline": mid
                })
            return None

        # Edge fade entry logic
        # 1. Fade Channel Low: swept or touched LL boundary, turned up, RSI oversold
        edge_zone = ll + width * 0.15
        if curr_close <= edge_zone and curr_close > prev_close and rsi < 32.0:
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=80.0,
                take_profit_bps=110.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_low_rebound",
                    "price": curr_close,
                    "channel_low": ll,
                    "midline": mid,
                    "rsi": rsi,
                    "width_pct": width_pct,
                }
            )

        # 2. Fade Channel High: swept or touched HH boundary, turned down, RSI overbought
        edge_zone_high = hh - width * 0.15
        if curr_close >= edge_zone_high and curr_close < prev_close and rsi > 68.0:
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=80.0,
                take_profit_bps=110.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_high_rejection",
                    "price": curr_close,
                    "channel_high": hh,
                    "midline": mid,
                    "rsi": rsi,
                    "width_pct": width_pct,
                }
            )

        return None