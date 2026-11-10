from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeMeanReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger_b(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None, None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return None, None, None, None
        upper = mean + num_std * std
        lower = mean - num_std * std
        bandwidth = (upper - lower) / mean
        pct_b = (closes[-1] - lower) / (upper - lower)
        return pct_b, mean, bandwidth, std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Prior bar Bollinger values
        prev_closes = closes[:-1]
        prev_pct_b, _, _, _ = self._bollinger_b(prev_closes, self.bb_period, self.bb_std)
        curr_pct_b, mid_band, bandwidth, std = self._bollinger_b(closes, self.bb_period, self.bb_std)

        if prev_pct_b is None or curr_pct_b is None or mid_band is None:
            return None

        curr_close = closes[-1]
        pos = ctx.position_direction()

        # Exit logic: reversion to mid-band
        if pos == "long":
            if curr_close >= mid_band or curr_pct_b >= 0.50:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_midband_reached",
                    "close": curr_close,
                    "mid_band": mid_band,
                    "pct_b": curr_pct_b
                })
        elif pos == "short":
            if curr_close <= mid_band or curr_pct_b <= 0.50:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_midband_reached",
                    "close": curr_close,
                    "mid_band": mid_band,
                    "pct_b": curr_pct_b
                })

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # If already positioned, avoid multiple entries
        if ctx.has_position():
            return None

        # Volatility state & cross-asset filter to solve prior OOS failure
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Relative volatility: avoid entering during extreme band squeeze or extreme blowout
        if bandwidth < 0.025 or bandwidth > 0.14:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Long: failed band break (prior closed below band pct_b < 0, current closes back inside pct_b >= 0.05 and <= 0.35)
        # Cross-asset confirm: BTC not in severe dump (btc_ret > -3.0%)
        if prev_pct_b < 0.0 and (0.02 <= curr_pct_b <= 0.35) and btc_ret > -3.0:
            confidence = min(0.85, 0.55 + max(0.0, 0.25 - curr_pct_b))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_rejection_reentry",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "bandwidth": round(bandwidth, 4),
                    "btc_ret": round(btc_ret, 2),
                    "close": curr_close
                }
            )

        # Short: failed upper band break (prior closed above band pct_b > 1.0, current closes back inside pct_b <= 0.98 and >= 0.65)
        # Cross-asset confirm: BTC not in strong surge (btc_ret < 3.0%)
        if prev_pct_b > 1.0 and (0.65 <= curr_pct_b <= 0.98) and btc_ret < 3.0:
            confidence = min(0.85, 0.55 + max(0.0, curr_pct_b - 0.75))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_rejection_reentry",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "bandwidth": round(bandwidth, 4),
                    "btc_ret": round(btc_ret, 2),
                    "close": curr_close
                }
            )

        return None