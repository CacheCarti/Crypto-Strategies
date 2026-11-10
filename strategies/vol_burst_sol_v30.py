from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period + 5:
            return None

        highs = ctx.highs(self.period + 5)
        lows = ctx.lows(self.period + 5)
        volumes = ctx.volumes(self.period + 5)

        # Handle open position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_close = ctx.bar.close
            curr_open = ctx.bar.open
            pos_dir = ctx.position_direction()

            # Time-based hold expiration
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exit_reached", "bars_held": bars_held, "price": curr_close}
                )

            # Momentum stall: bar closed strongly against the trade direction
            if pos_dir == "long" and curr_close < curr_open and (curr_open - curr_close) > 0.6 * (ctx.bar.high - ctx.bar.low):
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={"reason": "long_momentum_stall", "bars_held": bars_held, "price": curr_close}
                )
            elif pos_dir == "short" and curr_close > curr_open and (curr_close - curr_open) > 0.6 * (ctx.bar.high - ctx.bar.low):
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={"reason": "short_momentum_stall", "bars_held": bars_held, "price": curr_close}
                )
            return None

        # Check post-exit mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume condition: current volume > 2.2x the 20-bar rolling average volume
        avg_vol = sum(volumes[-self.period - 1:-1]) / self.period
        if avg_vol <= 0:
            return None
        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol
        if vol_ratio < 2.2:
            return None

        # Volatility / Range condition: current bar range > 1.8x the 14-bar ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        current_range = ctx.bar.high - ctx.bar.low
        range_ratio = current_range / atr
        if range_ratio < 1.8:
            return None

        # Candle body conviction: body must be at least 60% of full candle range
        body = abs(ctx.bar.close - ctx.bar.open)
        if current_range == 0 or (body / current_range) < 0.60:
            return None

        # Prior evidence note: Sol volume bursts are high conviction when entering along trend
        sma_fast = sum(closes[-10:]) / 10.0
        sma_slow = sum(closes[-self.period:]) / self.period

        # Bullish burst: strong close above open and positive trend confirmation
        if ctx.bar.close > ctx.bar.open and ctx.bar.close > sma_fast and sma_fast >= sma_slow:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (vol_ratio - 2.2) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_burst_bullish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "price": ctx.bar.close,
                    "atr": round(atr, 4)
                }
            )

        # Bearish burst: strong close below open and downward trend confirmation
        elif ctx.bar.close < ctx.bar.open and ctx.bar.close < sma_fast and sma_fast <= sma_slow:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (vol_ratio - 2.2) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_burst_bearish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "price": ctx.bar.close,
                    "atr": round(atr, 4)
                }
            )

        return None