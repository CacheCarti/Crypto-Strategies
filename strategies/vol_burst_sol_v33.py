from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.max_hold_bars = 5
        self.cooldown_bars = 4
        self.bars_in_pos = 0
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_in_pos = 0
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        volumes = ctx.volumes(self.lookback + 2)

        if len(closes) < self.lookback + 1 or len(volumes) < self.lookback + 1:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()

            # Time-based exhaustion exit
            if self.bars_in_pos >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_bars_exhaustion",
                    "bars_held": self.bars_in_pos,
                    "close": ctx.bar.close
                })

            # Momentum stall: opposing strong close
            bar_change = ctx.bar.close - ctx.bar.open
            if direction == "long" and bar_change < 0 and abs(bar_change) > (ctx.bar.high - ctx.bar.low) * 0.65:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "momentum_stalled_bearish_reversal",
                    "bars_held": self.bars_in_pos
                })
            elif direction == "short" and bar_change > 0 and bar_change > (ctx.bar.high - ctx.bar.low) * 0.65:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "momentum_stalled_bullish_reversal",
                    "bars_held": self.bars_in_pos
                })
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid crisis liquidity breakdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        curr_vol = ctx.bar.volume
        recent_vols = volumes[-(self.lookback + 1):-1]
        vol_mean = sum(recent_vols) / len(recent_vols)
        if vol_mean <= 0:
            return None
        vol_ratio = curr_vol / vol_mean

        # Percentile rank of current volume in lookback window
        sorted_vols = sorted(recent_vols)
        vol_pct = sum(1 for v in sorted_vols if curr_vol >= v) / len(sorted_vols)

        # Range expansion relative to ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        bar_range = ctx.bar.high - ctx.bar.low
        range_to_atr = bar_range / atr

        # Require top-decile volume rank (or >2.2x mean) and strong range expansion
        is_vol_burst = (vol_ratio >= 2.1) and (vol_pct >= 0.90)
        is_range_expansion = range_to_atr >= 1.65

        if not (is_vol_burst and is_range_expansion):
            return None

        # Body dominance: ensure directional commitment (body >= 55% of full candle range)
        body = abs(ctx.bar.close - ctx.bar.open)
        if bar_range <= 0 or (body / bar_range) < 0.55:
            return None

        # Entry signal based on breakout direction
        if ctx.bar.close > ctx.bar.open:
            # Confirm close is near high (upper 25% of bar)
            if (ctx.bar.high - ctx.bar.close) / bar_range <= 0.25:
                self.bars_in_pos = 0
                return ctx.signal(
                    "long",
                    confidence=min(0.85, 0.55 + 0.15 * min(vol_ratio / 3.0, 1.0)),
                    stop_loss_bps=280.0,
                    take_profit_bps=520.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sol_bullish_volume_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_to_atr": round(range_to_atr, 2),
                        "bar_index": ctx.bar_index
                    }
                )
        elif ctx.bar.close < ctx.bar.open:
            # Confirm close is near low (lower 25% of bar)
            if (ctx.bar.close - ctx.bar.low) / bar_range <= 0.25:
                self.bars_in_pos = 0
                return ctx.signal(
                    "short",
                    confidence=min(0.85, 0.55 + 0.15 * min(vol_ratio / 3.0, 1.0)),
                    stop_loss_bps=280.0,
                    take_profit_bps=520.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sol_bearish_volume_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_to_atr": round(range_to_atr, 2),
                        "bar_index": ctx.bar_index
                    }
                )

        return None