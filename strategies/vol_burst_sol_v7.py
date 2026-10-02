from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SOL Volume Burst Momentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 18
        self.rsi_period = 14
        self.vol_multiplier = 2.1
        self.atr_range_mult = 1.75
        self.body_ratio_min = 0.55
        self.cooldown_bars = 7
        self.max_hold_bars = 5
        
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period: int = 14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        vol_avg = self._sma(volumes, self.vol_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)

        if vol_avg is None or atr is None or rsi is None or atr <= 0.0:
            return None

        current_vol = ctx.bar.volume
        bar_range = ctx.bar.high - ctx.bar.low
        body_size = abs(ctx.bar.close - ctx.bar.open)
        body_ratio = body_size / bar_range if bar_range > 0 else 0.0
        vol_ratio = current_vol / vol_avg if vol_avg > 0 else 0.0
        range_atr_ratio = bar_range / atr

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time-based expiration
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_horizon_exit",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                    }
                )

            # Momentum exhaustion / stall exit
            if pos_dir == "long" and (rsi > 78.0 or (ctx.bar.close < ctx.bar.open and vol_ratio > 1.4)):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_momentum_stall",
                        "rsi": round(rsi, 2),
                        "vol_ratio": round(vol_ratio, 2),
                    }
                )

            if pos_dir == "short" and (rsi < 22.0 or (ctx.bar.close > ctx.bar.open and vol_ratio > 1.4)):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_momentum_stall",
                        "rsi": round(rsi, 2),
                        "vol_ratio": round(vol_ratio, 2),
                    }
                )

            return None

        # Check cooldown before opening new positions
        if ctx.bar_index - self.last_exit_bar <= self.cooldown_bars:
            return None

        # Volume burst + wide range expansion conditions
        is_vol_burst = vol_ratio >= self.vol_multiplier
        is_wide_range = range_atr_ratio >= self.atr_range_mult
        is_solid_body = body_ratio >= self.body_ratio_min

        if not (is_vol_burst and is_wide_range and is_solid_body):
            return None

        # Bullish momentum breakout
        if ctx.bar.close > ctx.bar.open and rsi < 72.0:
            confidence = min(0.9, 0.55 + (vol_ratio - self.vol_multiplier) * 0.1)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(range_atr_ratio, 2),
                    "body_ratio": round(body_ratio, 2),
                    "rsi": round(rsi, 2),
                }
            )

        # Bearish momentum breakout
        if ctx.bar.close < ctx.bar.open and rsi > 28.0:
            confidence = min(0.9, 0.55 + (vol_ratio - self.vol_multiplier) * 0.1)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(range_atr_ratio, 2),
                    "body_ratio": round(body_ratio, 2),
                    "rsi": round(rsi, 2),
                }
            )

        return None