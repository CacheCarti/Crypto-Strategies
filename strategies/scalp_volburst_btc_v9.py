from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class VolumeBurstContinuationScalp(Strategy):
    METADATA = {
        "name": "VolumeBurstContinuationScalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 14
        self.trend_ema_period = 40
        self.vol_multiplier = 3.8
        self.min_range_atr_ratio = 1.5
        self.min_body_ratio = 0.65
        self.max_hold_bars = 4
        self.cooldown_bars = 40
        
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.entry_direction = None

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

    def _atr(self, highs: list, lows: list, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.entry_bar = -999
        self.entry_direction = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.vol_period, self.atr_period, self.trend_ema_period) + 5
        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        volumes = ctx.volumes(warmup)

        if len(closes) < warmup or len(volumes) < warmup:
            return None

        current_idx = ctx.bar_index
        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume

        # Time-based exit for open position
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_max_hold_reached",
                        "bars_held": bars_held,
                        "close": current_close
                    }
                )
            return None

        # Hard multi-bar cooldown guard
        if current_idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # Filter out high-stress crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Baseline volume, ATR, and trend filters
        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        trend_ema = self._ema(closes, self.trend_ema_period)

        if avg_vol is None or atr is None or trend_ema is None or avg_vol <= 0 or atr <= 0:
            return None

        bar_range = current_high - current_low
        if bar_range <= 0:
            return None

        body_size = abs(current_close - current_open)
        body_ratio = body_size / bar_range

        # Decisive momentum candle required (avoid long wicks / rejection dojis)
        if body_ratio < self.min_body_ratio:
            return None

        vol_ratio = current_vol / avg_vol
        range_to_atr = bar_range / atr

        # Require an undeniable volume surge and wide range expansion
        if vol_ratio < self.vol_multiplier or range_to_atr < self.min_range_atr_ratio:
            return None

        # Close position relative to the bar range [0.0 = extreme low, 1.0 = extreme high]
        close_location = (current_close - current_low) / bar_range

        # Funding rate filter to prevent entering heavily crowded counter-moves
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Bullish volume burst: top 15% close, green body, price above trend EMA
        if (
            close_location >= 0.85 
            and current_close > current_open 
            and current_close > trend_ema 
            and funding < 0.0003
        ):
            confidence = min(0.92, 0.60 + 0.08 * (vol_ratio / self.vol_multiplier))
            self.last_trade_bar = current_idx
            self.entry_bar = current_idx
            self.entry_direction = "long"
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "selective_volume_burst_bullish_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(range_to_atr, 2),
                    "body_ratio": round(body_ratio, 2),
                    "close_location": round(close_location, 3),
                    "funding_rate": funding
                }
            )

        # Bearish volume burst: bottom 15% close, red body, price below trend EMA
        if (
            close_location <= 0.15 
            and current_close < current_open 
            and current_close < trend_ema 
            and funding > -0.0003
        ):
            confidence = min(0.92, 0.60 + 0.08 * (vol_ratio / self.vol_multiplier))
            self.last_trade_bar = current_idx
            self.entry_bar = current_idx
            self.entry_direction = "short"
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "selective_volume_burst_bearish_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(range_to_atr, 2),
                    "body_ratio": round(body_ratio, 2),
                    "close_location": round(close_location, 3),
                    "funding_rate": funding
                }
            )

        return None