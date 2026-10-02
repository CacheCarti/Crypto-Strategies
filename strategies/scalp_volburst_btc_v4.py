from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class VolumeBurstScalp(Strategy):
    METADATA = {
        "name": "BTC 5m Volume Burst Continuation Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 190.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 14
        self.trend_ema_period = 50
        self.vol_multiplier = 3.85
        self.min_range_atr_ratio = 1.55
        self.top_quartile = 0.85
        self.bottom_quartile = 0.15
        self.max_hold_bars = 5
        self.cooldown_bars = 20
        self.bars_in_trade = 0
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Manage active position: time decay exit
        if ctx.has_position():
            self.bars_in_trade += 1
            if self.bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_decay_exit",
                        "bars_held": self.bars_in_trade,
                        "close": ctx.bar.close,
                    },
                )
            return None
        else:
            self.bars_in_trade = 0

        # Strict post-exit cooldown to avoid churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out choppy or crisis market regimes
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.35:
            return None

        needed_bars = max(self.trend_ema_period, self.vol_period, self.atr_period) + 5
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)
        volumes = ctx.volumes(needed_bars)

        if len(closes) < needed_bars or len(volumes) < needed_bars:
            return None

        # Compute benchmark baseline volume (excluding current breakout bar)
        avg_vol = sum(volumes[-(self.vol_period + 1):-1]) / self.vol_period
        if avg_vol <= 0:
            return None

        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol
        if vol_ratio < self.vol_multiplier:
            return None

        # Range expansion filter via ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_high = ctx.bar.high
        bar_low = ctx.bar.low
        bar_close = ctx.bar.close
        bar_open = ctx.bar.open
        bar_range = bar_high - bar_low

        if bar_range <= 0 or (bar_range / atr) < self.min_range_atr_ratio:
            return None

        # Trend alignment filter: EMA 50
        ema_trend = self._ema(closes, self.trend_ema_period)
        if ema_trend is None:
            return None

        close_location = (bar_close - bar_low) / bar_range
        confidence = min(0.65 + (vol_ratio - self.vol_multiplier) * 0.06, 0.95)

        # Bullish Burst: High volume + top quartile close + solid green body + above trend EMA
        if close_location >= self.top_quartile and bar_close > bar_open and bar_close > ema_trend:
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_trend_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "close_location": round(close_location, 3),
                    "range_to_atr": round(bar_range / atr, 2),
                    "ema50": round(ema_trend, 2),
                    "close": bar_close,
                },
            )

        # Bearish Burst: High volume + bottom quartile close + solid red body + below trend EMA
        if close_location <= self.bottom_quartile and bar_close < bar_open and bar_close < ema_trend:
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_trend_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "close_location": round(close_location, 3),
                    "range_to_atr": round(bar_range / atr, 2),
                    "ema50": round(ema_trend, 2),
                    "close": bar_close,
                },
            )

        return None