from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _bollinger_pct_b(self, closes):
        if len(closes) < self.period:
            return None, None, None, None
        slice_c = closes[-self.period:]
        mean = sum(slice_c) / self.period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.period
        std = math.sqrt(variance)
        if std == 0:
            return None, None, None, None
        upper = mean + self.num_std * std
        lower = mean - self.num_std * std
        bandwidth = upper - lower
        if bandwidth <= 0:
            return None, None, None, None
        pct_b = (closes[-1] - lower) / bandwidth
        return pct_b, mean, upper, lower

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Avoid extreme market stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Calculate current and previous %B
        curr_pct_b, curr_mid, curr_up, curr_low = self._bollinger_pct_b(closes)
        prev_pct_b, _, _, _ = self._bollinger_pct_b(closes[:-1])

        if curr_pct_b is None or prev_pct_b is None:
            return None

        current_price = ctx.bar.close

        # Position management / Dynamic Mid-band exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_price >= curr_mid:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_midband_reached",
                    "price": current_price,
                    "mid": curr_mid,
                    "pct_b": curr_pct_b
                })
            elif pos_dir == "short" and current_price <= curr_mid:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_midband_reached",
                    "price": current_price,
                    "mid": curr_mid,
                    "pct_b": curr_pct_b
                })
            return None

        # Enforce cooldown between trades to avoid overtrading/cluster failures
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        rsi_val = self._rsi(ctx.closes(20), period=14)
        if rsi_val is None:
            return None

        # Failed lower band break: previously < 0, now closed back above lower band (pct_b > 0)
        # Filter with mild RSI bounds to avoid fighting runaway persistent trends
        if prev_pct_b < 0.0 and 0.0 <= curr_pct_b <= 0.25 and rsi_val >= 25.0:
            confidence = min(1.0, 0.55 + max(0.0, (0.25 - curr_pct_b) * 1.2))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "lower_band_reentry_fade",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "curr_pct_b": round(curr_pct_b, 4),
                    "rsi": round(rsi_val, 2),
                    "price": current_price,
                    "mid": round(curr_mid, 2)
                }
            )

        # Failed upper band break: previously > 1.0, now closed back below upper band (pct_b < 1.0)
        if prev_pct_b > 1.0 and 0.75 <= curr_pct_b <= 1.0 and rsi_val <= 75.0:
            confidence = min(1.0, 0.55 + max(0.0, (curr_pct_b - 0.75) * 1.2))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "upper_band_reentry_fade",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "curr_pct_b": round(curr_pct_b, 4),
                    "rsi": round(rsi_val, 2),
                    "price": current_price,
                    "mid": round(curr_mid, 2)
                }
            )

        return None