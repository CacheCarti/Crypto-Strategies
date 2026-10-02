from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SOL Volume Burst Momentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 580.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 22
        self.atr_period = 18
        self.rsi_period = 14
        self.vol_multiplier = 2.2
        self.range_multiplier = 1.75
        self.min_body_ratio = 0.55
        self.max_hold_bars = 6
        self.cooldown_bars = 8

        self.entry_bar_idx = -1
        self.last_exit_bar_idx = -100

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / float(period)

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / float(period)

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / float(period)
        avg_loss = sum(losses[-period:]) / float(period)
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = (
                ctx.bar_index - self.entry_bar_idx
                if self.entry_bar_idx > 0
                else 0
            )
            rsi = self._rsi(closes, self.rsi_period) or 50.0

            # Time-based exit limit
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_horizon_expiry",
                        "bars_held": bars_held,
                        "rsi": rsi,
                        "close": ctx.bar.close,
                    },
                )

            # Momentum stall / overextension exit
            if pos_dir == "long" and rsi >= 78.0:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_momentum_stall_rsi_overbought",
                        "rsi": rsi,
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and rsi <= 22.0:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_momentum_stall_rsi_oversold",
                        "rsi": rsi,
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )

            return None

        # Mandatory post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar_idx) < self.cooldown_bars:
            return None

        # Regime safety check
        if ctx.regime == "crisis":
            return None

        # Indicator calculations
        vol_sma = self._sma(volumes, self.vol_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)

        if vol_sma is None or atr is None or rsi is None or vol_sma <= 0:
            return None

        current_vol = ctx.bar.volume
        bar_range = ctx.bar.high - ctx.bar.low
        body = abs(ctx.bar.close - ctx.bar.open)

        # Volume burst & range expansion checks
        vol_ratio = current_vol / vol_sma
        is_vol_burst = vol_ratio >= self.vol_multiplier
        is_range_expanded = bar_range >= (atr * self.range_multiplier)
        has_solid_body = (
            bar_range > 0 and (body / bar_range) >= self.min_body_ratio
        )

        if not (is_vol_burst and is_range_expanded and has_solid_body):
            return None

        # Bullish momentum impulse
        if (
            ctx.bar.close > ctx.bar.open
            and rsi < 72.0
            and ctx.bar.close > closes[-2]
        ):
            confidence = min(
                0.85, 0.55 + 0.1 * (vol_ratio / self.vol_multiplier)
            )
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(bar_range / atr, 2),
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close,
                },
            )

        # Bearish momentum impulse
        if (
            ctx.bar.close < ctx.bar.open
            and rsi > 28.0
            and ctx.bar.close < closes[-2]
        ):
            confidence = min(
                0.85, 0.55 + 0.1 * (vol_ratio / self.vol_multiplier)
            )
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(bar_range / atr, 2),
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close,
                },
            )

        return None

    def on_position_close(
        self, ctx: BarContext, position: Dict[str, Any]
    ) -> None:
        self.last_exit_bar_idx = ctx.bar_index
        self.entry_bar_idx = -1