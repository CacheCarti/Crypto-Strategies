from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.cooldown_bars = 8
        self.max_hold_bars = 6
        self.bars_in_position = 0
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_position += 1
            direction = ctx.position_direction()
            curr_bar = ctx.bar
            bar_body = abs(curr_bar.close - curr_bar.open)
            bar_range = max(curr_bar.high - curr_bar.low, 1e-6)

            # Exit if momentum reverses / exhausts (doji or adverse candle)
            stalled = False
            if direction == "long":
                if curr_bar.close < curr_bar.open or (bar_body / bar_range < 0.25 and self.bars_in_position >= 3):
                    stalled = True
            elif direction == "short":
                if curr_bar.close > curr_bar.open or (bar_body / bar_range < 0.25 and self.bars_in_position >= 3):
                    stalled = True

            if stalled or self.bars_in_position >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_or_stall_exit",
                        "bars_in_pos": self.bars_in_position,
                        "stalled": stalled,
                        "close": curr_bar.close
                    }
                )
            return None

        # Enforce cooldown after prior exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Compute volume metrics
        recent_vols = volumes[-self.vol_period:]
        avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0
        curr_vol = ctx.bar.volume
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1.0

        # Compute ATR and range metrics
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_bar = ctx.bar
        curr_range = curr_bar.high - curr_bar.low
        body = abs(curr_bar.close - curr_bar.open)
        range_ratio = curr_range / atr

        # Event boundary: Volume burst (>2.2x avg) and Wide Range Bar (>1.8x ATR)
        # Strong directional close (body at least 65% of entire bar range)
        is_burst = vol_ratio >= 2.2 and range_ratio >= 1.8 and (body / max(curr_range, 1e-6)) >= 0.65

        if not is_burst:
            return None

        # Check funding rate sanity to avoid crowded positioning
        funding = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long Trigger: Strong green candle with close near high
        if curr_bar.close > curr_bar.open and (curr_bar.close - curr_bar.low) / max(curr_range, 1e-6) >= 0.75:
            if funding < 0.0004:  # Avoid excessive positive funding squeeze
                conf = min(0.5 + 0.1 * (vol_ratio - 2.2) + 0.1 * (range_ratio - 1.8), 0.95)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_burst_bull_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "funding": funding,
                        "close": curr_bar.close
                    }
                )

        # Short Trigger: Strong red candle with close near low
        elif curr_bar.close < curr_bar.open and (curr_bar.high - curr_bar.close) / max(curr_range, 1e-6) >= 0.75:
            if funding > -0.0004:  # Avoid entering shorts into extreme negative funding
                conf = min(0.5 + 0.1 * (vol_ratio - 2.2) + 0.1 * (range_ratio - 1.8), 0.95)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_burst_bear_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "funding": funding,
                        "close": curr_bar.close
                    }
                )

        return None