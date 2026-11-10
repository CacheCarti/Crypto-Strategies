from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 bars on 1h swing
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.max_hold_bars = 5
        self.cooldown_bars = 8
        self.entry_bar_idx = -100
        self.last_exit_idx = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_idx = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        volumes = ctx.volumes(30)

        if len(closes) < self.METADATA["warmup_bars"] or len(volumes) < self.vol_period + 1:
            return None

        # Check existing position for time-based exit or momentum stall
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            curr_close = ctx.bar.close
            curr_open = ctx.bar.open
            direction = ctx.position_direction()

            # Stalling logic: reversal bar or hold limit reached
            stalled = False
            if bars_held >= self.max_hold_bars:
                stalled = True
            elif direction == "long" and curr_close < curr_open and bars_held >= 2:
                # Long momentum stalled on sharp counter bar
                stalled = True
            elif direction == "short" and curr_close > curr_open and bars_held >= 2:
                # Short momentum stalled on sharp counter bar
                stalled = True

            if stalled:
                self.last_exit_idx = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "momentum_stalled_or_max_bars", "bars_held": bars_held, "direction": direction}
                )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_idx) < self.cooldown_bars:
            return None

        # Market regime filter: avoid choppy crisis/meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Compute volume metrics
        past_vols = volumes[-(self.vol_period + 1):-1]
        mean_vol = sum(past_vols) / len(past_vols)
        current_vol = volumes[-1]

        if mean_vol <= 0:
            return None

        vol_ratio = current_vol / mean_vol

        # Compute range vs ATR metrics
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        current_range = ctx.bar.high - ctx.bar.low
        range_ratio = current_range / atr

        # Bar directional displacement (body size vs total range)
        body = abs(ctx.bar.close - ctx.bar.open)
        body_to_range = body / current_range if current_range > 0 else 0

        # High-conviction volume-burst:
        # 1. Volume >= 2.2x average
        # 2. Range >= 1.6x ATR
        # 3. Decisive candle body >= 55% of total bar range
        if vol_ratio >= 2.2 and range_ratio >= 1.6 and body_to_range >= 0.55:
            # Bullish burst: close near highs
            if ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.low) / current_range >= 0.70:
                self.entry_bar_idx = ctx.bar_index
                conf = min(0.5 + (vol_ratio - 2.0) * 0.1, 0.95)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_bull_vol_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_to_range": round(body_to_range, 2),
                        "atr": round(atr, 4),
                        "close": ctx.bar.close
                    }
                )

            # Bearish burst: close near lows
            elif ctx.bar.close < ctx.bar.open and (ctx.bar.high - ctx.bar.close) / current_range >= 0.70:
                self.entry_bar_idx = ctx.bar_index
                conf = min(0.5 + (vol_ratio - 2.0) * 0.1, 0.95)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_bear_vol_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_to_range": round(body_to_range, 2),
                        "atr": round(atr, 4),
                        "close": ctx.bar.close
                    }
                )

        return None