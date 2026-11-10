from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _bollinger_pct_b(self, closes):
        if len(closes) < self.bb_period:
            return None, None, None, None
        slice_c = closes[-self.bb_period:]
        mean = sum(slice_c) / self.bb_period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.bb_period
        std = math.sqrt(variance)
        upper = mean + self.bb_std * std
        lower = mean - self.bb_std * std
        band_width = upper - lower
        if band_width <= 0:
            return None, None, None, None
        pct_b = (closes[-1] - lower) / band_width
        return pct_b, mean, upper, lower

    def _rsi(self, closes):
        if len(closes) < self.rsi_period + 1:
            return None
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
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Prior bar %B and Current bar %B
        curr_pct_b, curr_mid, curr_upper, curr_lower = self._bollinger_pct_b(closes)
        prev_pct_b, _, _, _ = self._bollinger_pct_b(closes[:-1])

        if curr_pct_b is None or prev_pct_b is None:
            return None

        current_price = ctx.bar.close

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit at mid-band reversion target
            if pos_dir == "long" and current_price >= curr_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "target_hit_bb_mid", "price": current_price, "mid": curr_mid, "pct_b": curr_pct_b}
                )
            elif pos_dir == "short" and current_price <= curr_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "target_hit_bb_mid", "price": current_price, "mid": curr_mid, "pct_b": curr_pct_b}
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter - avoid fade during high crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi_val = self._rsi(ctx.closes(self.rsi_period + 5))
        if rsi_val is None:
            return None

        # Long Setup: Prior bar pierced lower band (< 0.0), current bar closed back inside (> 0.05)
        # Filter: RSI confirms washed out state (RSI < 45) but not collapsing
        if prev_pct_b < 0.0 and curr_pct_b >= 0.05 and curr_pct_b < 0.40 and rsi_val < 45:
            conf = min(0.9, max(0.55, (0.45 - curr_pct_b) * 1.5 + 0.3))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_reentry_long_fade",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": curr_pct_b,
                    "rsi": rsi_val,
                    "mid": curr_mid,
                    "lower": curr_lower,
                }
            )

        # Short Setup: Prior bar pierced upper band (> 1.0), current bar closed back inside (< 0.95)
        # Filter: RSI confirms overextended rally (RSI > 55)
        if prev_pct_b > 1.0 and curr_pct_b <= 0.95 and curr_pct_b > 0.60 and rsi_val > 55:
            conf = min(0.9, max(0.55, (curr_pct_b - 0.55) * 1.5 + 0.3))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_reentry_short_fade",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": curr_pct_b,
                    "rsi": rsi_val,
                    "mid": curr_mid,
                    "upper": curr_upper,
                }
            )

        return None