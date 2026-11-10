from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.vol_mult = 2.2
        self.range_mult = 1.6
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        volumes = ctx.volumes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25 or len(volumes) < 25:
            return None

        # Check existing position for time-based or momentum-stall exit
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            pos_dir = ctx.position_direction()

            # Time stop: exit after fixed duration
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exit_reached", "bars_in_trade": bars_in_trade, "close": ctx.bar.close}
                )

            # Momentum stall: opposing directional rejection or inside stall
            current_bar_move = ctx.bar.close - ctx.bar.open
            if pos_dir == "long" and bars_in_trade >= 2:
                if current_bar_move < 0 and ctx.bar.close < closes[-2]:
                    return ctx.signal(
                        "flat",
                        confidence=0.55,
                        metadata={"reason": "momentum_stall_bearish_close", "close": ctx.bar.close}
                    )
            elif pos_dir == "short" and bars_in_trade >= 2:
                if current_bar_move > 0 and ctx.bar.close > closes[-2]:
                    return ctx.signal(
                        "flat",
                        confidence=0.55,
                        metadata={"reason": "momentum_stall_bullish_close", "close": ctx.bar.close}
                    )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate volume threshold
        vol_slice = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(vol_slice) / len(vol_slice) if vol_slice else 0.0
        if avg_vol <= 0:
            return None

        curr_vol = ctx.bar.volume
        vol_ratio = curr_vol / avg_vol

        # Calculate range and ATR
        atr = self._atr(highs, lows, closes, period=self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_range = ctx.bar.high - ctx.bar.low
        range_ratio = curr_range / atr

        # Core setup: Volume expansion AND abnormally wide range bar
        if vol_ratio >= self.vol_mult and range_ratio >= self.range_mult:
            body = abs(ctx.bar.close - ctx.bar.open)
            body_ratio = body / curr_range if curr_range > 0 else 0.0

            # Filter out non-directional dojis / large wick rejections
            if body_ratio < 0.45:
                return None

            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Scaled confidence
            conf = min(0.85, 0.55 + (vol_ratio - 2.0) * 0.05 + (range_ratio - 1.5) * 0.05)

            if ctx.bar.close > ctx.bar.open:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=round(conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "volume_burst_bullish_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "close": ctx.bar.close
                    }
                )
            elif ctx.bar.close < ctx.bar.open:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=round(conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "volume_burst_bearish_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "close": ctx.bar.close
                    }
                )

        return None