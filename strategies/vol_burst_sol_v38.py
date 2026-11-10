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
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.cooldown_bars = 6
        self.max_hold_bars = 5

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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        if len(closes) < 30:
            return None

        current_idx = ctx.bar_index

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = current_idx - self.entry_bar
            curr_bar = ctx.bar

            # Momentum exhaustion / stall exit or max hold time
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_exhaustion_exit", "bars_held": bars_held})

            # Early stall check: bar closes against entry direction with notable range
            if pos_dir == "long" and curr_bar.close < curr_bar.open and (curr_bar.open - curr_bar.close) > (curr_bar.high - curr_bar.low) * 0.6:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "long_momentum_stall", "bars_held": bars_held})
            elif pos_dir == "short" and curr_bar.close > curr_bar.open and (curr_bar.close - curr_bar.open) > (curr_bar.high - curr_bar.low) * 0.6:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "short_momentum_stall", "bars_held": bars_held})

            return None

        # Cooldown guard after exits
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: Skip extreme meltdown or crisis states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Compute Volume & ATR baselines
        vol_slice = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(vol_slice) / len(vol_slice) if len(vol_slice) > 0 else 1.0
        cur_vol = volumes[-1]

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_range = highs[-1] - lows[-1]
        body = abs(closes[-1] - ctx.opens(1)[0])

        # Core Conditions: Volume burst (>2.2x), wide range (>1.6x ATR), decisive body (>60% of candle range)
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0.0
        range_ratio = bar_range / atr

        if vol_ratio < 2.2 or range_ratio < 1.6 or (bar_range > 0 and body / bar_range < 0.60):
            return None

        # Cross-Asset Confirmation (Addressing prior failure family: vol_burst_sol)
        # SOL moves must be aligned with or confirmed by broader crypto momentum (BTC 24h return)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Directional triggers
        curr_close = closes[-1]
        curr_open = ctx.opens(1)[0]
        direction = None

        if curr_close > curr_open and btc_ret > -1.5:
            # Bullish expansion candle, BTC not dragging heavily downward
            direction = "long"
        elif curr_close < curr_open and btc_ret < 1.5:
            # Bearish expansion candle, BTC not pumping heavily upward
            direction = "short"

        if direction is not None:
            self.entry_bar = current_idx
            conf = min(0.9, 0.5 + (vol_ratio - 2.0) * 0.08 + (range_ratio - 1.5) * 0.08)
            return ctx.signal(
                direction,
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=18000,
                metadata={
                    "reason": f"sol_vol_burst_{direction}",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "btc_return_pct": round(btc_ret, 2),
                    "bar_body_ratio": round(body / bar_range, 2) if bar_range > 0 else 0,
                }
            )

        return None