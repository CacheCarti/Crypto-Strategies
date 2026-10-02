from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "VolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 20
        self.ema_trend_period = 50
        self.vol_multiplier = 2.85
        self.atr_multiplier = 2.10
        self.min_body_ratio = 0.65
        self.max_hold_bars = 8
        self.cooldown_bars = 16
        self.entry_bar_idx = -1
        self.last_exit_bar = -999

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = max(self.vol_period, self.atr_period, self.ema_trend_period) + 5
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)
        opens = ctx.opens(req_bars)
        volumes = ctx.volumes(req_bars)

        if len(closes) < req_bars or len(volumes) < self.vol_period + 1:
            return None

        # Position Management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 1
            direction = ctx.position_direction()
            current_bar = ctx.bar

            # Time stop / max hold check
            if bars_in_pos >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "max_bars_reached",
                    "bars_held": bars_in_pos,
                    "close": current_bar.close
                })

            # Check momentum stall after at least 2 bars in trade
            if bars_in_pos >= 2:
                bar_range = current_bar.high - current_bar.low
                if bar_range > 0:
                    if direction == "long" and (current_bar.close < current_bar.open) and ((current_bar.open - current_bar.close) > bar_range * 0.75):
                        self.last_exit_bar = ctx.bar_index
                        return ctx.signal("flat", confidence=0.65, metadata={
                            "reason": "momentum_stalled_heavy_bear_bar",
                            "bars_held": bars_in_pos,
                            "close": current_bar.close
                        })
                    elif direction == "short" and (current_bar.close > current_bar.open) and ((current_bar.close - current_bar.open) > bar_range * 0.75):
                        self.last_exit_bar = ctx.bar_index
                        return ctx.signal("flat", confidence=0.65, metadata={
                            "reason": "momentum_stalled_heavy_bull_bar",
                            "bars_held": bars_in_pos,
                            "close": current_bar.close
                        })

            return None

        # Hard Cooldown Guard after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown states
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate indicators
        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        ema_trend = self._ema(closes, self.ema_trend_period)

        if avg_vol is None or atr is None or ema_trend is None or avg_vol <= 0 or atr <= 0:
            return None

        curr_bar = ctx.bar
        curr_vol = curr_bar.volume
        curr_range = curr_bar.high - curr_bar.low
        curr_body = abs(curr_bar.close - curr_bar.open)

        if curr_range <= 0:
            return None

        vol_ratio = curr_vol / avg_vol
        range_ratio = curr_range / atr
        body_fraction = curr_body / curr_range

        # Strict selective volume & range burst filter
        if vol_ratio < self.vol_multiplier or range_ratio < self.atr_multiplier or body_fraction < self.min_body_ratio:
            return None

        # Long Entry: Strong bullish candle breaking clearly above EMA trend
        if curr_bar.close > curr_bar.open and curr_bar.close > (ema_trend * 1.0025):
            self.entry_bar_idx = ctx.bar_index
            confidence = min(1.0, 0.60 + 0.20 * min((vol_ratio - self.vol_multiplier) / 2.0, 1.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "selective_bull_volume_burst",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "body_fraction": round(body_fraction, 2),
                    "ema_trend": round(ema_trend, 2),
                    "close": round(curr_bar.close, 2)
                }
            )

        # Short Entry: Strong bearish candle breaking clearly below EMA trend
        if curr_bar.close < curr_bar.open and curr_bar.close < (ema_trend * 0.9975):
            self.entry_bar_idx = ctx.bar_index
            confidence = min(1.0, 0.60 + 0.20 * min((vol_ratio - self.vol_multiplier) / 2.0, 1.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "selective_bear_volume_burst",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "body_fraction": round(body_fraction, 2),
                    "ema_trend": round(ema_trend, 2),
                    "close": round(curr_bar.close, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_idx = -1