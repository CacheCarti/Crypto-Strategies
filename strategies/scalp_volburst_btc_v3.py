from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstContinuationScalp(Strategy):
    METADATA = {
        "name": "BTC 5m Volume Burst Continuation Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.ema_period = 50
        self.vol_mult = 3.6
        self.range_atr_mult = 1.6
        self.max_hold_bars = 4
        self.cooldown_bars = 28
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        history_len = max(self.vol_period, self.ema_period) + 10
        closes = ctx.closes(history_len)
        highs = ctx.highs(history_len)
        lows = ctx.lows(history_len)
        volumes = ctx.volumes(history_len)

        if len(closes) < history_len:
            return None

        # Position management: time-based exit after brief momentum window
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "burst_time_decay_exit",
                        "bars_held": bars_held,
                        "close_price": ctx.bar.close
                    }
                )
            return None

        # Hard cooldown to prevent overtrading and fee drag
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip dangerous market regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        ema_val = self._ema(closes, self.ema_period)

        if avg_vol is None or avg_vol <= 0 or atr_val is None or atr_val <= 0 or ema_val is None:
            return None

        current_vol = ctx.bar.volume
        vol_ratio = current_vol / avg_vol
        bar_range = ctx.bar.high - ctx.bar.low

        if bar_range <= 0:
            return None

        # Measure where bar closed within its total range (0.0 = low, 1.0 = high)
        close_pos = (ctx.bar.close - ctx.bar.low) / bar_range

        # Require significant volume burst (>= 3.6x) and range expansion (>= 1.6x ATR)
        if vol_ratio >= self.vol_mult and bar_range >= (self.range_atr_mult * atr_val):
            # Bullish burst: close in upper 12% and aligned with macro EMA
            if close_pos >= 0.88 and ctx.bar.close > ema_val:
                self.entry_bar = ctx.bar_index
                confidence = min(0.95, 0.70 + (vol_ratio - self.vol_mult) * 0.05)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=85.0,
                    take_profit_bps=160.0,
                    horizon_seconds=900,
                    metadata={
                        "reason": "selective_vol_burst_bullish_top_decile",
                        "vol_ratio": round(vol_ratio, 2),
                        "close_pos": round(close_pos, 3),
                        "range_atr_ratio": round(bar_range / atr_val, 2),
                        "price": ctx.bar.close,
                        "ema50": round(ema_val, 2)
                    }
                )

            # Bearish burst: close in lower 12% and aligned below macro EMA
            elif close_pos <= 0.12 and ctx.bar.close < ema_val:
                self.entry_bar = ctx.bar_index
                confidence = min(0.95, 0.70 + (vol_ratio - self.vol_mult) * 0.05)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=85.0,
                    take_profit_bps=160.0,
                    horizon_seconds=900,
                    metadata={
                        "reason": "selective_vol_burst_bearish_bottom_decile",
                        "vol_ratio": round(vol_ratio, 2),
                        "close_pos": round(close_pos, 3),
                        "range_atr_ratio": round(bar_range / atr_val, 2),
                        "price": ctx.bar.close,
                        "ema50": round(ema_val, 2)
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index