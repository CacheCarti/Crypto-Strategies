from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 16
        self.breakout_period = 16
        self.vol_mult = 2.85
        self.range_mult = 1.95
        self.min_body_ratio = 0.65
        self.max_hold_bars = 8
        self.cooldown_bars = 12
        
        self.entry_bar_idx = -999
        self.last_exit_bar_idx = -999

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs: list, lows: list, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return None
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.vol_period, self.atr_period, self.breakout_period) + 5
        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        volumes = ctx.volumes(warmup)

        if len(closes) < warmup:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume
        bar_idx = ctx.bar_index

        atr_val = self._atr(highs, lows, closes, self.atr_period)
        vol_avg = self._sma(volumes[:-1], self.vol_period)
        rsi_val = self._rsi(closes, 14)

        if atr_val is None or vol_avg is None or vol_avg <= 0 or atr_val <= 0:
            return None

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = bar_idx - self.entry_bar_idx

            # Time-based expiration
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar_idx = bar_idx
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "max_bars_exhaustion",
                        "bars_held": bars_held,
                        "rsi": round(rsi_val, 2) if rsi_val else 50.0,
                        "close": current_close
                    }
                )

            # Momentum stall / reversal detection
            if direction == "long":
                if (rsi_val is not None and rsi_val > 80.0) or (current_close < current_open and (current_high - current_close) > 1.4 * atr_val):
                    self.last_exit_bar_idx = bar_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_momentum_stall",
                            "bars_held": bars_held,
                            "rsi": round(rsi_val, 2) if rsi_val else 50.0,
                            "close": current_close
                        }
                    )
            elif direction == "short":
                if (rsi_val is not None and rsi_val < 20.0) or (current_close > current_open and (current_close - current_low) > 1.4 * atr_val):
                    self.last_exit_bar_idx = bar_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_momentum_stall",
                            "bars_held": bars_held,
                            "rsi": round(rsi_val, 2) if rsi_val else 50.0,
                            "close": current_close
                        }
                    )
            return None

        # Multi-bar hard cooldown after exit
        if (bar_idx - self.last_exit_bar_idx) < self.cooldown_bars:
            return None

        # Filter out extreme meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Evaluate volume-burst and wide-range expansion
        vol_ratio = current_vol / vol_avg
        bar_range = current_high - current_low
        body_size = abs(current_close - current_open)
        range_ratio = bar_range / atr_val

        is_volume_burst = vol_ratio >= self.vol_mult
        is_wide_range = range_ratio >= self.range_mult
        is_solid_body = bar_range > 0 and (body_size / bar_range) >= self.min_body_ratio

        if is_volume_burst and is_wide_range and is_solid_body:
            lookback_high = max(highs[-(self.breakout_period + 1):-1])
            lookback_low = min(lows[-(self.breakout_period + 1):-1])

            # Bullish expansion with fresh range breakout
            if current_close > current_open and current_close >= lookback_high:
                self.entry_bar_idx = bar_idx
                confidence = min(0.90, 0.65 + (vol_ratio / 15.0))
                return ctx.signal(
                    "long",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_volume_burst_breakout",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_size / bar_range, 2),
                        "atr": round(atr_val, 4),
                        "price": current_close
                    }
                )

            # Bearish expansion with fresh range breakdown
            elif current_close < current_open and current_close <= lookback_low:
                self.entry_bar_idx = bar_idx
                confidence = min(0.90, 0.65 + (vol_ratio / 15.0))
                return ctx.signal(
                    "short",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_volume_burst_breakdown",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_size / bar_range, 2),
                        "atr": round(atr_val, 4),
                        "price": current_close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar_idx = ctx.bar_index