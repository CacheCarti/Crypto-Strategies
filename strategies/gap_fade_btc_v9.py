from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WickRejectionReversion(Strategy):
    METADATA = {
        "name": "Wick Rejection Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.vol_period = 20
        self.rsi_period = 14
        self.wick_threshold = 0.58
        self.range_mult = 1.20
        self.vol_mult = 1.25
        self.cooldown_bars = 5
        self.max_hold_bars = 4
        self.last_trade_bar = -999
        self.entry_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 5)
        highs = ctx.highs(self.warmup_bars + 5)
        lows = ctx.lows(self.warmup_bars + 5)
        volumes = ctx.volumes(self.warmup_bars + 5)

        if len(closes) < self.warmup_bars:
            return None

        # Manage open position exit logic
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_hold_horizon_reached",
                    "bars_in_trade": bars_in_trade,
                    "close": ctx.bar.close
                })
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        avg_vol = self._sma(volumes, self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or avg_vol is None or rsi is None or atr <= 0.0 or avg_vol <= 0.0:
            return None

        bar_high = ctx.bar.high
        bar_low = ctx.bar.low
        bar_open = ctx.bar.open
        bar_close = ctx.bar.close
        bar_vol = ctx.bar.volume

        bar_range = bar_high - bar_low
        if bar_range <= 0.0:
            return None

        upper_wick = bar_high - max(bar_open, bar_close)
        lower_wick = min(bar_open, bar_close) - bar_low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range
        range_rel_atr = bar_range / atr
        vol_rel_avg = bar_vol / avg_vol

        is_large_range = range_rel_atr >= self.range_mult
        is_elevated_volume = vol_rel_avg >= self.vol_mult

        # Rejection of highs -> Upper wick exhaustion -> Short
        if is_large_range and is_elevated_volume and upper_wick_ratio >= self.wick_threshold and rsi >= 45.0:
            confidence = min(0.90, 0.60 + 0.20 * (upper_wick_ratio - self.wick_threshold) + 0.10 * min(vol_rel_avg - 1.0, 1.0))
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "high_rejection_upper_wick_fade",
                    "upper_wick_ratio": round(upper_wick_ratio, 3),
                    "range_rel_atr": round(range_rel_atr, 2),
                    "vol_rel_avg": round(vol_rel_avg, 2),
                    "rsi": round(rsi, 1),
                    "atr": round(atr, 2),
                    "close": bar_close
                }
            )

        # Rejection of lows -> Lower wick exhaustion -> Long
        if is_large_range and is_elevated_volume and lower_wick_ratio >= self.wick_threshold and rsi <= 55.0:
            confidence = min(0.90, 0.60 + 0.20 * (lower_wick_ratio - self.wick_threshold) + 0.10 * min(vol_rel_avg - 1.0, 1.0))
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "low_rejection_lower_wick_fade",
                    "lower_wick_ratio": round(lower_wick_ratio, 3),
                    "range_rel_atr": round(range_rel_atr, 2),
                    "vol_rel_avg": round(vol_rel_avg, 2),
                    "rsi": round(rsi, 1),
                    "atr": round(atr, 2),
                    "close": bar_close
                }
            )

        return None