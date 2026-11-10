from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 bars on 1h
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
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
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        volumes = ctx.volumes(30)

        if len(closes) < 25 or len(volumes) < 25:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_dir = ctx.position_direction()
            
            # Momentum stall / time exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_bars_reached",
                    "bars_held": bars_held
                })

            # Exit if candle reverses strongly against direction
            body = ctx.bar.close - ctx.bar.open
            if curr_dir == "long" and body < 0 and abs(body) > (ctx.bar.high - ctx.bar.low) * 0.7:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_momentum_stall",
                    "bars_held": bars_held
                })
            elif curr_dir == "short" and body > 0 and body > (ctx.bar.high - ctx.bar.low) * 0.7:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_momentum_stall",
                    "bars_held": bars_held
                })

            return None

        # Cooldown guard after last exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate to address prior OOS failures:
        # Avoid low-conviction regimes or crisis breakdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Volume burst check: current volume > 2.0x 20-bar baseline
        vol_slice = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(vol_slice) / len(vol_slice) if vol_slice else 0.0
        if avg_vol <= 0:
            return None

        current_vol = ctx.bar.volume
        vol_ratio = current_vol / avg_vol
        if vol_ratio < 2.0:
            return None

        # Wide range check: range > 1.6x ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_range = ctx.bar.high - ctx.bar.low
        if curr_range < 1.6 * atr:
            return None

        # Candle body must dominate the range (strong conviction, low wick stall)
        body = ctx.bar.close - ctx.bar.open
        if abs(body) < 0.60 * curr_range:
            return None

        # Align with broader macro/cross-asset momentum (BTC 24h return filter)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Directional entry
        if body > 0 and trend_regime != "bear" and btc_ret > -3.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.6 + (vol_ratio - 2.0) * 0.1),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                metadata={
                    "reason": "bullish_vol_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(curr_range / atr, 2),
                    "btc_return_pct": btc_ret,
                    "trend_regime": trend_regime
                }
            )
        elif body < 0 and trend_regime != "bull" and btc_ret < 3.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.6 + (vol_ratio - 2.0) * 0.1),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                metadata={
                    "reason": "bearish_vol_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(curr_range / atr, 2),
                    "btc_return_pct": btc_ret,
                    "trend_regime": trend_regime
                }
            )

        return None