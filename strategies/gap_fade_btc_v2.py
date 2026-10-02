from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcWickReversion(Strategy):
    METADATA = {
        "name": "BtcWickReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.vol_period = 24
        self.rsi_period = 14
        self.wick_ratio_threshold = 0.65
        self.opposite_wick_max = 0.15
        self.atr_expansion_mult = 1.55
        self.vol_expansion_mult = 1.50
        self.cooldown_bars = 10
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        return sum(trs[-period:]) / period

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.atr_period, self.vol_period, self.rsi_period) + 10
        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        volumes = ctx.volumes(warmup)

        if len(closes) < warmup:
            return None

        bar = ctx.bar
        bar_range = bar.high - bar.low
        if bar_range <= 0:
            return None

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_hold_time_reached",
                        "bars_held": bars_held,
                        "close": bar.close,
                    },
                )
            return None

        # Enforce strict multi-bar post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid extreme instability
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.50 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Key indicator calculations
        atr = self._atr(highs, lows, closes, self.atr_period)
        vol_sma = self._sma(volumes, self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or vol_sma is None or rsi is None or atr <= 0 or vol_sma <= 0:
            return None

        # High-conviction expansion requirements
        is_large_range = bar_range >= (atr * self.atr_expansion_mult)
        is_high_volume = bar.volume >= (vol_sma * self.vol_expansion_mult)

        if not (is_large_range and is_high_volume):
            return None

        # Wick proportion calculations
        body_top = max(bar.open, bar.close)
        body_bottom = min(bar.open, bar.close)
        upper_wick = bar.high - body_top
        lower_wick = body_bottom - bar.low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range

        # Long Entry: Strong bottom wick rejection with low-to-moderate RSI (dip exhaustion)
        if (
            lower_wick_ratio >= self.wick_ratio_threshold
            and upper_wick_ratio <= self.opposite_wick_max
            and rsi <= 48.0
        ):
            confidence = min(0.90, 0.60 + (lower_wick_ratio - self.wick_ratio_threshold) * 0.9)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "lower_wick_exhaustion_long",
                    "lower_wick_ratio": round(lower_wick_ratio, 3),
                    "upper_wick_ratio": round(upper_wick_ratio, 3),
                    "range_to_atr": round(bar_range / atr, 2),
                    "vol_to_avg": round(bar.volume / vol_sma, 2),
                    "rsi": round(rsi, 2),
                    "price": bar.close,
                },
            )

        # Short Entry: Strong top wick rejection with high-to-moderate RSI (rally exhaustion)
        if (
            upper_wick_ratio >= self.wick_ratio_threshold
            and lower_wick_ratio <= self.opposite_wick_max
            and rsi >= 52.0
        ):
            confidence = min(0.90, 0.60 + (upper_wick_ratio - self.wick_ratio_threshold) * 0.9)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "upper_wick_exhaustion_short",
                    "upper_wick_ratio": round(upper_wick_ratio, 3),
                    "lower_wick_ratio": round(lower_wick_ratio, 3),
                    "range_to_atr": round(bar_range / atr, 2),
                    "vol_to_avg": round(bar.volume / vol_sma, 2),
                    "rsi": round(rsi, 2),
                    "price": bar.close,
                },
            )

        return None