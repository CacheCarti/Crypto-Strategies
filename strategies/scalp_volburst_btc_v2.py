from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcVolumeBurstContinuation(Strategy):
    METADATA = {
        "name": "BTC Volume Burst Continuation Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 25
        self.atr_period = 14
        self.vol_threshold = 3.2
        self.min_range_atr_ratio = 0.55
        self.cooldown_bars = 12
        self.max_hold_bars = 4

        self.last_exit_bar = -999
        self.bars_in_trade = 0

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Manage open position time-based exit
        if ctx.has_position():
            self.bars_in_trade += 1
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "burst_time_decay_exit",
                        "bars_held": self.bars_in_trade,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Check cooldown after previous exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        avg_vol = self._sma(volumes[:-1], self.vol_period)
        if avg_vol is None or avg_vol <= 0:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        cur_bar = ctx.bar
        vol_ratio = cur_bar.volume / avg_vol
        candle_range = cur_bar.high - cur_bar.low

        if candle_range <= 0 or vol_ratio < self.vol_threshold:
            return None

        # Ensure candle has significant physical expansion
        if candle_range < (atr * self.min_range_atr_ratio):
            return None

        pos_in_bar = (cur_bar.close - cur_bar.low) / candle_range
        confidence = min(0.9, 0.55 + 0.08 * (vol_ratio - self.vol_threshold))

        # Bullish volume expansion: closes near the top quartile with green body
        if pos_in_bar >= 0.80 and cur_bar.close > cur_bar.open:
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=85.0,
                take_profit_bps=160.0,
                metadata={
                    "reason": "bullish_volume_burst_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "pos_in_bar": round(pos_in_bar, 3),
                    "atr": round(atr, 2),
                    "close": cur_bar.close,
                },
            )

        # Bearish volume expansion: closes near the bottom quartile with red body
        if pos_in_bar <= 0.20 and cur_bar.close < cur_bar.open:
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=85.0,
                take_profit_bps=160.0,
                metadata={
                    "reason": "bearish_volume_burst_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "pos_in_bar": round(pos_in_bar, 3),
                    "atr": round(atr, 2),
                    "close": cur_bar.close,
                },
            )

        return None