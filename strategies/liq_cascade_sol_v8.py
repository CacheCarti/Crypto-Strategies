from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolCascadeExhaustionReversal(Strategy):
    METADATA = {
        "name": "SOL Cascade Exhaustion Reversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 18
        self.vol_period = 24
        self.rsi_period = 14
        self.range_mult = 2.2
        self.vol_mult = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _atr(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Manage open position exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Take profit on strong overbought mean-reversion exhaustion
                if rsi > 70.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "rsi_overbought_mean_reversion_target",
                            "rsi": round(rsi, 2),
                            "price": round(current_price, 2),
                        },
                    )
            return None

        # Check cooldown to prevent over-trading
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Market regime filter - avoid entries during outright meltdown crises
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Calculate indicators over previous slice (excluding current forming bar)
        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        hist_closes = closes[:-1]
        hist_vols = volumes[:-1]

        atr = self._atr(hist_highs, hist_lows, hist_closes, self.atr_period)
        vol_avg = self._sma(hist_vols, self.vol_period)

        if atr is None or vol_avg is None or vol_avg <= 0 or atr <= 0:
            return None

        # Prior bar (panic bar) values
        p_open = hist_closes[-2] if len(hist_closes) >= 2 else ctx.opens(3)[-2]
        p_high = hist_highs[-1]
        p_low = hist_lows[-1]
        p_close = hist_closes[-1]
        p_vol = hist_vols[-1]
        p_range = p_high - p_low

        # Current bar (exhaustion / stabilization bar)
        c_low = ctx.bar.low
        c_open = ctx.bar.open

        # Cascade Conditions on Prior Bar:
        # 1. Bearish dump bar: close below open
        is_drop = p_close < p_open
        # 2. Elevated range (> 2.2x ATR)
        is_large_range = p_range >= (self.range_mult * atr)
        # 3. Elevated volume (> 2.0x average volume)
        is_large_vol = p_vol >= (self.vol_mult * vol_avg)
        # 4. Closed near the low (panic close in bottom 35% of bar range)
        closed_near_low = (p_close - p_low) <= (0.35 * p_range) if p_range > 0 else False

        is_cascade_bar = is_drop and is_large_range and is_large_vol and closed_near_low

        if not is_cascade_bar:
            return None

        # Stabilization Conditions on Current Bar:
        # 1. Holds panic low: current low does not significantly undercut prior panic low
        holds_low = c_low >= (p_low * 0.9975)
        # 2. Rebound confirmation: current close is above open or above prior close
        rebounding = (current_price > c_open) or (current_price > p_close)
        # 3. RSI is in an exhausted/oversold regime (< 45)
        rsi_exhausted = rsi < 45.0

        if holds_low and rebounding and rsi_exhausted:
            # Dynamic Stop Loss: placed beneath panic low with safety margin
            dist_to_panic_low = (current_price - p_low) / current_price
            dynamic_sl_bps = max(180.0, min(420.0, (dist_to_panic_low * 10000.0) + 35.0))
            dynamic_tp_bps = min(850.0, dynamic_sl_bps * 2.2)

            vol_ratio = p_vol / vol_avg
            range_ratio = p_range / atr
            confidence = min(0.90, max(0.60, 0.55 + 0.05 * min(vol_ratio, 3.5)))

            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "liquidation_cascade_bottom_exhaustion_long",
                    "panic_low": round(p_low, 2),
                    "range_ratio": round(range_ratio, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "rsi": round(rsi, 2),
                    "price": round(current_price, 2),
                    "sl_bps": round(dynamic_sl_bps, 1),
                    "tp_bps": round(dynamic_tp_bps, 1),
                },
            )

        return None