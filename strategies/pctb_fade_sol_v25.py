from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctbFade(Strategy):
    METADATA = {
        "name": "SolPctbFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _bollinger(self, closes):
        slice_c = closes[-self.period:]
        mean = sum(slice_c) / self.period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.period
        std = math.sqrt(variance)
        return mean, mean + self.num_std * std, mean - self.num_std * std

    def _rsi(self, closes):
        if len(closes) < self.rsi_period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-self.rsi_period:]) / self.rsi_period
        avg_loss = sum(losses[-self.rsi_period:]) / self.rsi_period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Prior bar Bollinger bands
        prev_closes = closes[:-1]
        p_mid, p_upper, p_lower = self._bollinger(prev_closes)
        prev_band_w = p_upper - p_lower
        if prev_band_w <= 0:
            return None
        prev_pctb = (prev_closes[-1] - p_lower) / prev_band_w

        # Current bar Bollinger bands
        curr_mid, curr_upper, curr_lower = self._bollinger(closes)
        curr_band_w = curr_upper - curr_lower
        if curr_band_w <= 0:
            return None
        curr_pctb = (closes[-1] - curr_lower) / curr_band_w

        current_rsi = self._rsi(ctx.closes(self.rsi_period + 5))

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion target reached: close at the middle band
            if direction == "long" and closes[-1] >= curr_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_mid_band_reached",
                    "price": closes[-1],
                    "mid_band": curr_mid,
                    "pctb": curr_pctb
                })
            elif direction == "short" and closes[-1] <= curr_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_mid_band_reached",
                    "price": closes[-1],
                    "mid_band": curr_mid,
                    "pctb": curr_pctb
                })
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis breakdowns
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: failed breakout re-entering the band with momentum confirmation
        # Long: previous bar penetrated lower band (%B < 0), current bar closed back inside (%B > 0.05)
        if prev_pctb < 0.0 and curr_pctb > 0.05 and current_rsi < 45.0:
            confidence = min(0.85, 0.55 + max(0.0, (40.0 - current_rsi) / 100.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_band_reentry",
                    "prev_pctb": prev_pctb,
                    "curr_pctb": curr_pctb,
                    "rsi": current_rsi,
                    "lower_band": curr_lower,
                    "mid_band": curr_mid
                }
            )

        # Short: previous bar penetrated upper band (%B > 1.0), current bar closed back inside (%B < 0.95)
        if prev_pctb > 1.0 and curr_pctb < 0.95 and current_rsi > 55.0:
            confidence = min(0.85, 0.55 + max(0.0, (current_rsi - 60.0) / 100.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_band_reentry",
                    "prev_pctb": prev_pctb,
                    "curr_pctb": curr_pctb,
                    "rsi": current_rsi,
                    "upper_band": curr_upper,
                    "mid_band": curr_mid
                }
            )

        return None