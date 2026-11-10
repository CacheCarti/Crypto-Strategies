from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)
        volumes = ctx.volumes(30)

        if len(closes) < 50 or len(volumes) < 25:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time stop: held for max bars
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": bars_held}
                )

            # Stall exit: opposite direction bar with decent range
            curr_c = ctx.bar.close
            curr_o = ctx.bar.open
            if pos_dir == "long" and curr_c < curr_o and bars_held >= 2:
                prev_c = closes[-2]
                if curr_c < prev_c:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "long_momentum_stall", "bars_held": bars_held}
                    )
            elif pos_dir == "short" and curr_c > curr_o and bars_held >= 2:
                prev_c = closes[-2]
                if curr_c > prev_c:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "short_momentum_stall", "bars_held": bars_held}
                    )
            return None

        # Mandatory cooldown between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Volume condition: current volume > 2.5x 20-bar baseline
        vol_baseline = sum(volumes[-21:-1]) / 20.0
        if vol_baseline <= 0:
            return None
        vol_ratio = ctx.bar.volume / vol_baseline
        if vol_ratio < 2.5:
            return None

        # Range condition: current bar range > 2.0x 14-bar ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        bar_range = ctx.bar.high - ctx.bar.low
        range_ratio = bar_range / atr
        if range_ratio < 2.0:
            return None

        # Body dominance: ensure directional conviction, not a high-vol spinning top
        body = abs(ctx.bar.close - ctx.bar.open)
        if bar_range == 0 or (body / bar_range) < 0.60:
            return None

        # S/R context: EMA 50 trend alignment
        ema_50 = sum(closes[-50:]) / 50.0

        # Long burst: strong bullish close above open and close near high
        if ctx.bar.close > ctx.bar.open and ctx.bar.close > ema_50:
            upper_wick = ctx.bar.high - ctx.bar.close
            if upper_wick <= 0.25 * bar_range:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.55 + (vol_ratio - 2.5) * 0.08)
                return ctx.signal(
                    "long",
                    confidence=round(conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "bullish_volume_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body / bar_range, 2),
                        "price": ctx.bar.close,
                        "ema_50": round(ema_50, 2),
                    }
                )

        # Short burst: strong bearish close below open and close near low
        elif ctx.bar.close < ctx.bar.open and ctx.bar.close < ema_50:
            lower_wick = ctx.bar.close - ctx.bar.low
            if lower_wick <= 0.25 * bar_range:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.55 + (vol_ratio - 2.5) * 0.08)
                return ctx.signal(
                    "short",
                    confidence=round(conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "bearish_volume_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body / bar_range, 2),
                        "price": ctx.bar.close,
                        "ema_50": round(ema_50, 2),
                    }
                )

        return None