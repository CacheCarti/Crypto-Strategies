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
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Compute current and previous Bollinger Bands
        curr_mid, curr_up, curr_low = self._bollinger(closes[-self.bb_period:], self.bb_period, self.bb_std)
        prev_mid, prev_up, prev_low = self._bollinger(closes[-self.bb_period-1:-1], self.bb_period, self.bb_std)

        if curr_mid is None or prev_mid is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        curr_range = curr_up - curr_low
        prev_range = prev_up - prev_low
        if curr_range <= 0 or prev_range <= 0:
            return None

        curr_pct_b = (curr_close - curr_low) / curr_range
        prev_pct_b = (prev_close - prev_low) / prev_range

        # Mid-band target exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= curr_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_mid_band_target_hit", "pct_b": curr_pct_b, "mid": curr_mid, "close": curr_close}
                )
            elif pos_dir == "short" and curr_close <= curr_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_mid_band_target_hit", "pct_b": curr_pct_b, "mid": curr_mid, "close": curr_close}
                )
            return None

        # Respect cooldown to limit overtrading and friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset confirmation & market sentiment filters (fixing the historical pctb_fade_sol failure mode)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Do not fade in high market crisis environments
        if crisis_score > 0.65:
            return None

        # Long Entry: Previous bar breached below lower band (prev_pct_b < 0.0)
        # Current bar recovers back inside (0.0 < curr_pct_b < 0.35)
        # Cross-asset filter: BTC 24h return isn't in freefall (> -5.0%)
        if prev_pct_b < 0.0 and 0.0 < curr_pct_b < 0.35 and btc_ret > -5.0 and fg_index > 15:
            conf = 0.70 if btc_ret > 0 else 0.60
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bollinger_pctb_lower_rejection",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "btc_ret": round(btc_ret, 2),
                    "fg_index": fg_index
                }
            )

        # Short Entry: Previous bar breached above upper band (prev_pct_b > 1.0)
        # Current bar rejects back inside (0.65 < curr_pct_b < 1.0)
        # Cross-asset filter: BTC 24h return isn't euphoric parabolic (< 5.0%)
        if prev_pct_b > 1.0 and 0.65 < curr_pct_b < 1.0 and btc_ret < 5.0 and fg_index < 85:
            conf = 0.70 if btc_ret < 0 else 0.60
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bollinger_pctb_upper_rejection",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "btc_ret": round(btc_ret, 2),
                    "fg_index": fg_index
                }
            )

        return None