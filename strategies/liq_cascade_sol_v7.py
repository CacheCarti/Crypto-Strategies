from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolLiquidationCascadeReversal(Strategy):
    METADATA = {
        "name": "SOL Liquidation Cascade Reversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.vol_period = 20
        self.rsi_period = 14
        self.atr_mult = 2.0
        self.vol_mult = 1.8
        self.close_tail_ratio = 0.35
        self.cooldown_bars = 6

        self.last_exit_bar = -999
        self.cascade_detected = False
        self.cascade_low = 0.0
        self.cascade_high = 0.0
        self.cascade_bar_index = -999

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.cascade_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Manage open position
        if ctx.has_position():
            rsi = self._rsi(closes, self.rsi_period)
            # Exit if RSI reaches overbought bounce territory
            if rsi is not None and rsi >= 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_rebound_target_reached", "rsi": round(rsi, 2), "price": ctx.bar.close},
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        vol_avg = self._sma(volumes, self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or vol_avg is None or rsi is None or atr <= 0.0 or vol_avg <= 0.0:
            return None

        # Check for exhaustion confirmation on the bar directly following a cascade
        if self.cascade_detected and (ctx.bar_index - self.cascade_bar_index == 1):
            held_low = ctx.bar.low >= self.cascade_low * 0.9985
            stabilized = ctx.bar.close > ctx.bar.open or ctx.bar.close >= closes[-2]

            if held_low and stabilized:
                fear_greed = ctx.features.get("fear_greed_index", 50)
                confidence = 0.75
                if rsi < 35.0:
                    confidence += 0.10
                if fear_greed < 40:
                    confidence += 0.05
                confidence = min(0.95, confidence)

                # Reset cascade tracker
                self.cascade_detected = False

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "liquidation_exhaustion_confirmed",
                        "cascade_low": round(self.cascade_low, 3),
                        "current_low": round(ctx.bar.low, 3),
                        "rsi": round(rsi, 2),
                        "fear_greed": fear_greed,
                        "price": round(ctx.bar.close, 3),
                    },
                )
            else:
                # Failed to hold the panic low, invalidate cascade
                self.cascade_detected = False

        # Cascade Detection on current bar
        bar_range = ctx.bar.high - ctx.bar.low
        is_bearish = ctx.bar.close < ctx.bar.open
        vol_ratio = ctx.bar.volume / vol_avg
        atr_ratio = bar_range / atr

        if bar_range > 0.0 and is_bearish:
            close_loc = (ctx.bar.close - ctx.bar.low) / bar_range
            if atr_ratio >= self.atr_mult and vol_ratio >= self.vol_mult and close_loc <= self.close_tail_ratio:
                self.cascade_detected = True
                self.cascade_low = ctx.bar.low
                self.cascade_high = ctx.bar.high
                self.cascade_bar_index = ctx.bar_index

        return None