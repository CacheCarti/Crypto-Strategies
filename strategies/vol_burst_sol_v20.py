from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.max_hold_bars = 6
        self.cooldown_period = 5
        self.last_exit_bar = -999
        self.entry_bar = 0

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.vol_period + 10)
        highs = ctx.highs(self.vol_period + 10)
        lows = ctx.lows(self.vol_period + 10)
        volumes = ctx.volumes(self.vol_period + 10)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            
            # Time-based hold expiration
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_bars_reached",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })

            # Momentum stall: opposing candle covering > 60% of previous bar after holding at least 2 bars
            if bars_held >= 2:
                prev_close = closes[-2]
                prev_open = ctx.opens(2)[-2]
                curr_body = ctx.bar.close - ctx.bar.open

                if pos_dir == "long" and curr_body < 0:
                    if abs(curr_body) >= 0.6 * max(1e-6, prev_close - prev_open):
                        return ctx.signal("flat", confidence=0.6, metadata={
                            "reason": "momentum_stall_bearish_reversal",
                            "bars_held": bars_held
                        })
                elif pos_dir == "short" and curr_body > 0:
                    if curr_body >= 0.6 * max(1e-6, prev_open - prev_close):
                        return ctx.signal("flat", confidence=0.6, metadata={
                            "reason": "momentum_stall_bullish_reversal",
                            "bars_held": bars_held
                        })
            return None

        # Cooldown guard: prevent overtrading after an exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Regime safety filter from market context
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)

        if avg_vol is None or atr is None or avg_vol <= 0 or atr <= 0:
            return None

        curr_bar = ctx.bar
        curr_vol = curr_bar.volume
        bar_range = curr_bar.high - curr_bar.low
        body = curr_bar.close - curr_bar.open

        # Selective Volume Burst: Volume > 2.0x average AND Bar Range > 1.75x ATR
        vol_ratio = curr_vol / avg_vol
        range_ratio = bar_range / atr

        if vol_ratio >= 2.1 and range_ratio >= 1.75:
            # Significant directional body relative to full candle range (filters out indecision wicks)
            body_ratio = abs(body) / max(bar_range, 1e-6)
            if body_ratio < 0.55:
                return None

            conf = min(0.9, 0.5 + (vol_ratio / 10.0))

            if body > 0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_vol_burst_momentum",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "close": curr_bar.close,
                        "atr": round(atr, 4)
                    }
                )
            elif body < 0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_vol_burst_momentum",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "close": curr_bar.close,
                        "atr": round(atr, 4)
                    }
                )

        return None