from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.range_period = 20
        self.max_hold_bars = 5
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        # Check existing position for timed exit or stalling
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exhaustion", "bars_held": bars_held}
                )

            # Momentum stall exit: counter-direction close after at least 2 bars
            if bars_held >= 2:
                bar_move = ctx.bar.close - ctx.bar.open
                if (pos_dir == "long" and bar_move < 0) or (pos_dir == "short" and bar_move > 0):
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "momentum_stall", "bars_held": bars_held, "move": bar_move}
                    )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        volumes = ctx.volumes(self.vol_period + 1)
        highs = ctx.highs(self.range_period + 1)
        lows = ctx.lows(self.range_period + 1)

        if len(volumes) < self.vol_period + 1 or len(highs) < self.range_period + 1:
            return None

        # Avoid crisis/meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Base volume & range indicators
        baseline_vols = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(baseline_vols) / len(baseline_vols)
        current_vol = volumes[-1]

        if avg_vol <= 0:
            return None

        vol_ratio = current_vol / avg_vol

        # Range expansion check via ATR
        atr = self._atr(highs, lows, closes, period=self.range_period)
        if atr is None or atr <= 0:
            return None

        current_range = ctx.bar.high - ctx.bar.low
        range_ratio = current_range / atr

        # Selective volume burst condition: volume > 2.2x average and range > 1.6x ATR
        if vol_ratio >= 2.2 and range_ratio >= 1.6:
            bar_delta = ctx.bar.close - ctx.bar.open
            delta_fraction = abs(bar_delta) / current_range if current_range > 0 else 0

            # Ensure conviction bar (body at least 55% of full range)
            if delta_fraction < 0.55:
                return None

            # Dynamic volatility-based stop loss
            dynamic_sl_bps = max(150.0, min(400.0, (atr / ctx.bar.close) * 10000.0 * 1.5))
            dynamic_tp_bps = dynamic_sl_bps * 2.0

            if bar_delta > 0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=min(1.0, 0.55 + (vol_ratio / 10.0)),
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=dynamic_tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_vol_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "atr": round(atr, 3),
                        "price": ctx.bar.close
                    }
                )
            elif bar_delta < 0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=min(1.0, 0.55 + (vol_ratio / 10.0)),
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=dynamic_tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_vol_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "atr": round(atr, 3),
                        "price": ctx.bar.close
                    }
                )

        return None