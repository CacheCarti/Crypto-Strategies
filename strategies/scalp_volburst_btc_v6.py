from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcVolumeBurstContinuation(Strategy):
    METADATA = {
        "name": "BtcVolumeBurstContinuation",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 30
        self.atr_period = 14
        self.vol_multiplier = 4.2
        self.cooldown_bars = 28
        self.max_hold_bars = 4
        self.entry_bar = -999
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Position management: time-based exit for short-lived impulse
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_decay_burst_expiry",
                        "bars_held": bars_held,
                        "exit_price": ctx.bar.close
                    }
                )
            return None

        # Hard cooldown after every exit to prevent overtrading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip during extreme market stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume baseline calculation
        vols = ctx.volumes(self.vol_period + 1)
        if len(vols) < self.vol_period + 1:
            return None

        prior_vols = vols[:-1]
        avg_vol = sum(prior_vols) / len(prior_vols)
        if avg_vol <= 0:
            return None

        curr_vol = ctx.bar.volume
        vol_ratio = curr_vol / avg_vol

        # Strict volume burst threshold (4.2x 30-bar baseline)
        if vol_ratio < self.vol_multiplier:
            return None

        bar_high = ctx.bar.high
        bar_low = ctx.bar.low
        bar_open = ctx.bar.open
        bar_close = ctx.bar.close
        bar_range = bar_high - bar_low

        if bar_range <= 0:
            return None

        # Volatility expansion check: bar range must be at least 1.5x ATR
        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)
        closes = ctx.closes(self.atr_period + 2)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if atr is None or bar_range < (1.5 * atr):
            return None

        # Candle body conviction: body must account for at least 60% of total candle range
        body_size = abs(bar_close - bar_open)
        if (body_size / bar_range) < 0.60:
            return None

        # Trend baseline filter (EMA 30)
        ema30 = self._ema(closes, 30)
        if ema30 is None:
            return None

        # Close Location Value (CLV): 0.0 (at low) to 1.0 (at high)
        clv = (bar_close - bar_low) / bar_range

        # Bullish burst: close in extreme top 12% + bullish candle + above EMA30
        if clv >= 0.88 and bar_close > bar_open and bar_close > ema30:
            confidence = min(0.90, 0.70 + (vol_ratio - self.vol_multiplier) * 0.04)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "selective_bullish_volume_burst",
                    "vol_ratio": round(vol_ratio, 2),
                    "clv": round(clv, 3),
                    "atr": round(atr, 2),
                    "ema30": round(ema30, 2),
                    "close": bar_close
                }
            )

        # Bearish burst: close in extreme bottom 12% + bearish candle + below EMA30
        elif clv <= 0.12 and bar_close < bar_open and bar_close < ema30:
            confidence = min(0.90, 0.70 + (vol_ratio - self.vol_multiplier) * 0.04)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "selective_bearish_volume_burst",
                    "vol_ratio": round(vol_ratio, 2),
                    "clv": round(clv, 3),
                    "atr": round(atr, 2),
                    "ema30": round(ema30, 2),
                    "close": bar_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index