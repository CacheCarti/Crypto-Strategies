from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Prior bar Bollinger
        prev_closes = closes[:-1]
        mid_prev, upper_prev, lower_prev = self._bollinger(prev_closes, self.period, self.num_std)
        # Current bar Bollinger
        mid_curr, upper_curr, lower_curr = self._bollinger(closes, self.period, self.num_std)

        if mid_prev is None or mid_curr is None:
            return None

        bw_prev = upper_prev - lower_prev
        bw_curr = upper_curr - lower_curr
        if bw_prev <= 0 or bw_curr <= 0:
            return None

        prev_pct_b = (prev_closes[-1] - lower_prev) / bw_prev
        curr_pct_b = (closes[-1] - lower_curr) / bw_curr

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit at mid-band
        if has_pos:
            if pos_dir == "long" and closes[-1] >= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_tp_mid_band", "pct_b": curr_pct_b, "mid": mid_curr})
            elif pos_dir == "short" and closes[-1] <= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_tp_mid_band", "pct_b": curr_pct_b, "mid": mid_curr})
            return None

        # Cooldown guard after position close
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading strong trending crisis regimes
        crisis = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: failed breakout back inside bands
        # Long: previous close was below lower band (%B < 0), current close reclaimed inside (%B >= 0 and <= 0.40)
        bullish_reversion = (prev_pct_b < 0.0) and (curr_pct_b >= 0.05) and (curr_pct_b <= 0.40)
        # Short: previous close was above upper band (%B > 1.0), current close fell back inside (%B <= 0.95 and >= 0.60)
        bearish_reversion = (prev_pct_b > 1.0) and (curr_pct_b <= 0.95) and (curr_pct_b >= 0.60)

        # Macro trend context: check simple 30-bar slope to avoid fading strong runaway trends
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if bullish_reversion and trend_regime != "bear":
            conf = 0.75 if trend_regime == "bull" else 0.65
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_reclaim",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "close": closes[-1],
                    "lower_curr": round(lower_curr, 2)
                }
            )

        if bearish_reversion and trend_regime != "bull":
            conf = 0.75 if trend_regime == "bear" else 0.65
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_reject",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "close": closes[-1],
                    "upper_curr": round(upper_curr, 2)
                }
            )

        return None