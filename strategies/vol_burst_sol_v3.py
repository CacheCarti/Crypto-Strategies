from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 14
        self.rsi_period = 14
        self.vol_multiplier = 2.15
        self.range_atr_multiplier = 1.65
        self.min_body_ratio = 0.58
        self.cooldown_bars = 7
        self.max_hold_bars = 6

        self.last_exit_bar = -100
        self.entry_bar = -100
        self.consecutive_losses = 0

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        return_bps = position.get("return_bps", 0.0)
        if return_bps < 0:
            self.consecutive_losses = min(self.consecutive_losses + 1, 3)
        else:
            self.consecutive_losses = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            rsi = self._rsi(closes, self.rsi_period)
            direction = ctx.position_direction()

            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_max_hold_reached",
                        "bars_held": bars_held,
                        "rsi": rsi,
                        "close": ctx.bar.close,
                    },
                )

            # Technical momentum stalling exit
            if rsi is not None:
                if direction == "long" and rsi > 78.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_overbought_exit",
                            "bars_held": bars_held,
                            "rsi": rsi,
                            "close": ctx.bar.close,
                        },
                    )
                elif direction == "short" and rsi < 22.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_oversold_exit",
                            "bars_held": bars_held,
                            "rsi": rsi,
                            "close": ctx.bar.close,
                        },
                    )

            return None

        # Mandatory cooldown filter
        adjusted_cooldown = self.cooldown_bars + self.consecutive_losses * 2
        if (ctx.bar_index - self.last_exit_bar) < adjusted_cooldown:
            return None

        # Platform regime protection
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)

        if avg_vol is None or atr is None or rsi is None or avg_vol <= 0 or atr <= 0:
            return None

        current_vol = ctx.bar.volume
        vol_ratio = current_vol / avg_vol
        bar_range = ctx.bar.high - ctx.bar.low
        if bar_range <= 0:
            return None

        range_atr_ratio = bar_range / atr
        bar_body = abs(ctx.bar.close - ctx.bar.open)
        body_ratio = bar_body / bar_range

        # Volume-burst and expansion gate
        if vol_ratio < self.vol_multiplier or range_atr_ratio < self.range_atr_multiplier or body_ratio < self.min_body_ratio:
            return None

        funding = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long Trigger: Strong green burst bar closing near highs
        upper_wick_ratio = (ctx.bar.high - ctx.bar.close) / bar_range
        if ctx.bar.close > ctx.bar.open and upper_wick_ratio <= 0.25 and rsi < 75.0 and funding < 0.0008:
            confidence = min(0.55 + (vol_ratio - self.vol_multiplier) * 0.08 + (range_atr_ratio - self.range_atr_multiplier) * 0.05, 0.90)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(range_atr_ratio, 2),
                    "body_ratio": round(body_ratio, 2),
                    "rsi": round(rsi, 2),
                    "price": ctx.bar.close,
                },
            )

        # Short Trigger: Strong red burst bar closing near lows
        lower_wick_ratio = (ctx.bar.close - ctx.bar.low) / bar_range
        if ctx.bar.close < ctx.bar.open and lower_wick_ratio <= 0.25 and rsi > 25.0 and funding > -0.0008:
            confidence = min(0.55 + (vol_ratio - self.vol_multiplier) * 0.08 + (range_atr_ratio - self.range_atr_multiplier) * 0.05, 0.90)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(range_atr_ratio, 2),
                    "body_ratio": round(body_ratio, 2),
                    "rsi": round(rsi, 2),
                    "price": ctx.bar.close,
                },
            )

        return None