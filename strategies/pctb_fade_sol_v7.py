from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPercentBReversion(Strategy):
    METADATA = {
        "name": "SolPercentBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 24
        self.bb_std = 2.35
        self.rsi_period = 14
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _bollinger(self, closes, period=24, num_std=2.35):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes, period=14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 5)
        if len(closes) < self.bb_period + 2:
            return None

        # Check market stress regime
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("MELTDOWN", "CRISIS") or crisis_score > 0.55:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={"reason": "crisis_exit", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        # Calculate current Bollinger Bands and %B
        mid, upper, lower = self._bollinger(closes, self.bb_period, self.bb_std)
        if mid is None or upper is None or lower is None or upper <= lower:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        band_width = upper - lower
        pct_b_curr = (curr_close - lower) / band_width

        # Calculate previous bar's Bollinger Bands
        prev_closes = closes[:-1]
        _, prev_upper, prev_lower = self._bollinger(prev_closes, self.bb_period, self.bb_std)
        if prev_upper is None or prev_lower is None or prev_upper <= prev_lower:
            return None

        prev_band_width = prev_upper - prev_lower
        pct_b_prev = (prev_close - prev_lower) / prev_band_width

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_solusdt", 0.0)

        # Handle active position management (Mean reversion target: Mid-band)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_tp_mid_band",
                        "close": curr_close,
                        "mid": mid,
                        "pct_b": pct_b_curr,
                        "rsi": rsi,
                    }
                )
            elif pos_dir == "short" and curr_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_tp_mid_band",
                        "close": curr_close,
                        "mid": mid,
                        "pct_b": pct_b_curr,
                        "rsi": rsi,
                    }
                )
            return None

        # Hard cooldown check after exit or prior entry
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_period or bars_since_entry < self.cooldown_period:
            return None

        # Long Setup: Deep piercing below lower band (%B < -0.02), sharp rejection back inside (%B in [0.05, 0.40]), oversold RSI
        if pct_b_prev < -0.02 and 0.05 <= pct_b_curr <= 0.40:
            if rsi < 38.0 and funding < 0.0003:
                self.last_entry_bar = ctx.bar_index
                confidence = 0.70
                if pct_b_prev < -0.10:
                    confidence += 0.10
                if rsi < 30.0:
                    confidence += 0.10
                confidence = min(0.95, confidence)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "pct_b_deep_lower_reentry",
                        "pct_b_prev": pct_b_prev,
                        "pct_b_curr": pct_b_curr,
                        "rsi": rsi,
                        "funding": funding,
                        "close": curr_close,
                        "lower_band": lower,
                    }
                )

        # Short Setup: Deep piercing above upper band (%B > 1.02), sharp rejection back inside (%B in [0.60, 0.95]), overbought RSI
        if pct_b_prev > 1.02 and 0.60 <= pct_b_curr <= 0.95:
            if rsi > 62.0 and funding > -0.0003:
                self.last_entry_bar = ctx.bar_index
                confidence = 0.70
                if pct_b_prev > 1.10:
                    confidence += 0.10
                if rsi > 70.0:
                    confidence += 0.10
                confidence = min(0.95, confidence)

                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "pct_b_deep_upper_reentry",
                        "pct_b_prev": pct_b_prev,
                        "pct_b_curr": pct_b_curr,
                        "rsi": rsi,
                        "funding": funding,
                        "close": curr_close,
                        "upper_band": upper,
                    }
                )

        return None