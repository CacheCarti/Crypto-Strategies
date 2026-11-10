from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 6
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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Manage active position: timed exit or momentum stalling
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_pos = ctx.position_direction()

            # Time-based structural exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "timed_structural_exit",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })

            # Momentum stall exit: bar closes opposite to position direction
            bar_dir = 1 if ctx.bar.close > ctx.bar.open else -1
            if (curr_pos == "long" and bar_dir == -1) or (curr_pos == "short" and bar_dir == 1):
                # Stalled momentum: reversal bar after at least 2 bars held
                if bars_held >= 2:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "momentum_stalled",
                        "bars_held": bars_held,
                        "close": ctx.bar.close
                    })
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Volume baseline calculation
        recent_vols = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(recent_vols) / self.vol_period if self.vol_period > 0 else 0.0
        if avg_vol <= 0:
            return None

        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol

        # Volatility & Range expansion
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_range = highs[-1] - lows[-1]
        range_ratio = bar_range / atr
        body = abs(closes[-1] - ctx.opens(1)[-1])
        body_to_range = body / bar_range if bar_range > 0 else 0.0

        # Strict volume burst setup:
        # 1. Volume >= 2.2x 20-bar avg
        # 2. Bar range >= 1.6x ATR
        # 3. Decisive body (body covers at least 65% of the range)
        if vol_ratio >= 2.2 and range_ratio >= 1.6 and body_to_range >= 0.65:
            open_price = ctx.opens(1)[-1]
            close_price = closes[-1]

            # Determine burst direction
            if close_price > open_price:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_burst_bullish_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_to_range, 2),
                        "close": close_price,
                        "atr": round(atr, 4)
                    }
                )
            elif close_price < open_price:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_burst_bearish_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_to_range, 2),
                        "close": close_price,
                        "atr": round(atr, 4)
                    }
                )

        return None