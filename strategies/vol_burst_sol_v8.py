from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 14
        self.trend_period = 50
        
        # Tightened thresholds to reach 30-80 trades target
        self.vol_mult = 2.8
        self.atr_mult = 2.1
        self.body_ratio_min = 0.65
        self.close_extreme_pct = 0.20
        
        self.max_hold_bars = 6
        self.cooldown_period = 12
        
        self.cooldown_until = 0
        self.entry_bar = 0
        self.entry_dir = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_period
        self.entry_dir = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = self.METADATA["warmup_bars"]
        if ctx.bar_index < warmup:
            return None

        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        opens = ctx.opens(warmup)
        volumes = ctx.volumes(warmup)

        if len(closes) < warmup:
            return None

        curr_close = ctx.bar.close
        curr_open = ctx.bar.open
        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_vol = ctx.bar.volume

        curr_range = curr_high - curr_low
        if curr_range <= 0:
            return None

        # Position management & early exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based hold expiration
            if bars_held >= self.max_hold_bars:
                self.cooldown_until = ctx.bar_index + self.cooldown_period
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_exit_reached",
                        "bars_held": bars_held,
                        "close": curr_close,
                    }
                )

            # Reversal / momentum failure exit
            if pos_dir == "long":
                if curr_close < curr_open and (curr_open - curr_close) > 0.65 * curr_range:
                    self.cooldown_until = ctx.bar_index + self.cooldown_period
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_stall",
                            "close": curr_close,
                            "open": curr_open,
                        }
                    )
            elif pos_dir == "short":
                if curr_close > curr_open and (curr_close - curr_open) > 0.65 * curr_range:
                    self.cooldown_until = ctx.bar_index + self.cooldown_period
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_stall",
                            "close": curr_close,
                            "open": curr_open,
                        }
                    )
            return None

        # Hard multi-bar cooldown after exit
        if ctx.bar_index < self.cooldown_until:
            return None

        # Regime filter: Skip extreme crisis to avoid false breakouts
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr_val = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        ema_trend = self._ema(closes[:-1], self.trend_period)

        if avg_vol is None or atr_val is None or ema_trend is None or avg_vol <= 0 or atr_val <= 0:
            return None

        vol_ratio = curr_vol / avg_vol
        range_ratio = curr_range / atr_val
        body_size = abs(curr_close - curr_open)
        body_ratio = body_size / curr_range

        # Strict burst criteria: exceptional volume, wide range, decisive directional body
        is_volume_burst = vol_ratio >= self.vol_mult
        is_wide_range = range_ratio >= self.atr_mult
        is_decisive_body = body_ratio >= self.body_ratio_min

        if not (is_volume_burst and is_wide_range and is_decisive_body):
            return None

        # Bullish Burst: closes near the top of the bar range and above trend EMA
        if curr_close > curr_open and (curr_close >= curr_high - self.close_extreme_pct * curr_range) and curr_close > ema_trend:
            self.entry_bar = ctx.bar_index
            self.entry_dir = "long"
            confidence = min(0.9, 0.65 + 0.1 * min(vol_ratio / 3.5, 1.0) + 0.1 * min(range_ratio / 3.0, 1.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "body_ratio": round(body_ratio, 2),
                    "ema_trend": round(ema_trend, 2),
                    "price": curr_close,
                }
            )

        # Bearish Burst: closes near the bottom of the bar range and below trend EMA
        elif curr_close < curr_open and (curr_close <= curr_low + self.close_extreme_pct * curr_range) and curr_close < ema_trend:
            self.entry_bar = ctx.bar_index
            self.entry_dir = "short"
            confidence = min(0.9, 0.65 + 0.1 * min(vol_ratio / 3.5, 1.0) + 0.1 * min(range_ratio / 3.0, 1.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_breakdown",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "body_ratio": round(body_ratio, 2),
                    "ema_trend": round(ema_trend, 2),
                    "price": curr_close,
                }
            )

        return None