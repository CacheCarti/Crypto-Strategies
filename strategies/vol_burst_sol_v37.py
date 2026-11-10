from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 20
        self.trend_period = 35
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.max_hold_bars = 6

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        highs = ctx.highs(45)
        lows = ctx.lows(45)
        volumes = ctx.volumes(45)

        if len(closes) < 42:
            return None

        # Check existing position for momentum exhaustion or time limit
        if ctx.has_position():
            pos = ctx.position or {}
            entry_price = pos.get("entry_price", ctx.bar.close)
            direction = ctx.position_direction()
            
            # Estimate bars held based on cooldown or simple proxy
            bars_held = ctx.bar_index - self.last_exit_bar if self.last_exit_bar > 0 else 1

            # Momentum stall exit: counter-bar with decent volume
            recent_vols = volumes[-(self.vol_period + 1):-1]
            avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0

            if direction == "long":
                if ctx.bar.close < ctx.bar.open and ctx.bar.volume > avg_vol * 1.2:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_momentum_stall",
                        "close": ctx.bar.close,
                        "open": ctx.bar.open,
                        "vol_ratio": round(ctx.bar.volume / avg_vol, 2)
                    })
            elif direction == "short":
                if ctx.bar.close > ctx.bar.open and ctx.bar.volume > avg_vol * 1.2:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_momentum_stall",
                        "close": ctx.bar.close,
                        "open": ctx.bar.open,
                        "vol_ratio": round(ctx.bar.volume / avg_vol, 2)
                    })
            return None

        # Post-exit cooldown gate to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Baseline metrics calculation
        baseline_vols = volumes[-(self.vol_period + 1):-1]
        avg_vol = sum(baseline_vols) / len(baseline_vols) if baseline_vols else 1.0
        if avg_vol <= 0:
            return None

        current_vol = ctx.bar.volume
        vol_ratio = current_vol / avg_vol

        # Volume burst threshold: > 2.2x the 20-bar average
        if vol_ratio < 2.2:
            return None

        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_range = ctx.bar.high - ctx.bar.low
        body_range = abs(ctx.bar.close - ctx.bar.open)

        # Expansion check: wide range (> 1.75x ATR) and decisive directional body (> 60% of candle range)
        if bar_range < 1.75 * atr or bar_range <= 0:
            return None

        body_ratio = body_range / bar_range
        if body_ratio < 0.60:
            return None

        # Structural trend alignment via EMA
        trend_ema = self._ema(closes, self.trend_period)
        if trend_ema is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Directional trigger
        if current_close > current_open and current_close > trend_ema:
            conf = min(0.90, 0.65 + (vol_ratio - 2.2) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bullish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(bar_range / atr, 2),
                    "body_ratio": round(body_ratio, 2),
                    "close": current_close,
                    "trend_ema": round(trend_ema, 2)
                }
            )

        elif current_close < current_open and current_close < trend_ema:
            conf = min(0.90, 0.65 + (vol_ratio - 2.2) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bearish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(bar_range / atr, 2),
                    "body_ratio": round(body_ratio, 2),
                    "close": current_close,
                    "trend_ema": round(trend_ema, 2)
                }
            )

        return None