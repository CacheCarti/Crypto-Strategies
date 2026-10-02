from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 150.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 40
        self.rsi_period = 9
        self.min_width_bps = 75.0
        self.max_width_bps = 145.0
        self.edge_threshold = 0.05
        self.cooldown_bars = 50
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period: int) -> Optional[float]:
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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)

        if len(closes) < self.channel_period or len(highs) < self.channel_period or len(lows) < self.channel_period:
            return None

        current_close = ctx.bar.close
        highest = max(highs)
        lowest = min(lows)
        channel_span = highest - lowest

        if current_close <= 0 or channel_span <= 0:
            return None

        mid = (highest + lowest) / 2.0
        width_bps = (channel_span / current_close) * 10000.0
        rsi = self._rsi(closes, self.rsi_period)

        if rsi is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()

            # Midline reversion exit
            if pos_dir == "long" and current_close >= mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_midline_target_hit",
                        "close": current_close,
                        "mid": mid,
                        "rsi": rsi,
                        "width_bps": width_bps,
                    },
                )
            elif pos_dir == "short" and current_close <= mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_midline_target_hit",
                        "close": current_close,
                        "mid": mid,
                        "rsi": rsi,
                        "width_bps": width_bps,
                    },
                )

            # Volatility breakout protection exit
            if width_bps > self.max_width_bps * 1.30:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "channel_volatility_breakout_exit",
                        "close": current_close,
                        "width_bps": width_bps,
                    },
                )

            return None

        # Hard post-exit cooldown to strictly limit trade frequency and friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Market regime gating: only trade in confirmed non-crisis, neutral tape
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.20:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime not in ("neutral", ""):
            return None

        # Tight channel restriction: ensure sideways chop, not active expansion
        if width_bps < self.min_width_bps or width_bps > self.max_width_bps:
            return None

        lower_bound = lowest + self.edge_threshold * channel_span
        upper_bound = highest - self.edge_threshold * channel_span

        # Selective Long Setup: extreme channel low with deeply oversold RSI confirmation
        if current_close <= lower_bound and rsi <= 23.0:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "deep_oversold_support_fade",
                    "close": current_close,
                    "channel_low": lowest,
                    "channel_high": highest,
                    "mid": mid,
                    "rsi": rsi,
                    "width_bps": width_bps,
                },
            )

        # Selective Short Setup: extreme channel high with deeply overbought RSI confirmation
        if current_close >= upper_bound and rsi >= 77.0:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "deep_overbought_resistance_fade",
                    "close": current_close,
                    "channel_low": lowest,
                    "channel_high": highest,
                    "mid": mid,
                    "rsi": rsi,
                    "width_bps": width_bps,
                },
            )

        return None