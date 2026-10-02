from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolLiquidationCascadeReversal(Strategy):
    METADATA = {
        "name": "SolLiquidationCascadeReversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 18
        self.vol_period = 18
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period):
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

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period):
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
        n_needed = max(self.atr_period, self.vol_period, self.rsi_period) + 5
        closes = ctx.closes(n_needed)
        if len(closes) < n_needed:
            return None

        highs = ctx.highs(n_needed)
        lows = ctx.lows(n_needed)
        opens = ctx.opens(n_needed)
        vols = ctx.volumes(n_needed)

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        # Position Management: active exit on mean reversion recovery
        if ctx.has_position():
            if current_rsi > 68.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "exhaustion_reversal_take_profit_rsi",
                        "rsi": round(current_rsi, 2),
                        "close": closes[-1],
                    },
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate indicators prior to confirmation bar
        atr_series = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        vol_sma_series = self._sma(vols[:-1], self.vol_period)

        if atr_series is None or vol_sma_series is None or vol_sma_series <= 0.0:
            return None

        # Examine previous bar (potential liquidation cascade panic bar)
        prev_open = opens[-2]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_vol = vols[-2]
        prev_range = prev_high - prev_low

        if prev_range <= 0.0:
            return None

        # Cascade Conditions:
        # 1. Bearish dump bar: close < open
        # 2. Wide range: range >= 2.2 * ATR
        # 3. High volume: volume >= 2.1 * average volume
        # 4. Closed near panic low: lower 35% of total range
        is_cascade = (
            prev_close < prev_open
            and prev_range >= (2.2 * atr_series)
            and prev_vol >= (2.1 * vol_sma_series)
            and ((prev_close - prev_low) / prev_range) <= 0.35
        )

        if not is_cascade:
            return None

        # Confirmation Bar (current bar):
        # 1. Low holds the panic bottom (does not make a significant new low)
        # 2. Price shows stabilization / green response or holds above cascade close
        curr_low = lows[-1]
        curr_close = closes[-1]
        curr_open = opens[-1]

        held_low = curr_low >= (prev_low * 0.9985)
        stabilized = (curr_close >= prev_close) or (curr_close >= curr_open)
        rsi_exhaustion = current_rsi <= 45.0

        if held_low and stabilized and rsi_exhaustion:
            # Dynamic Stop Loss just beneath cascade low
            risk_dist = max(closes[-1] - prev_low, closes[-1] * 0.015)
            calc_sl_bps = min(max((risk_dist / closes[-1]) * 10000.0 + 35.0, 180.0), 450.0)
            calc_tp_bps = min(calc_sl_bps * 2.2, 800.0)

            # Feature-assisted confidence boost
            funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
            fear_greed = ctx.features.get("fear_greed_index", 50.0)

            confidence = 0.70
            if funding_rate < 0.0:  # Negative funding indicates short crowding / cascade
                confidence += 0.10
            if fear_greed < 35.0:  # Extreme fear backdrop supports mean reversion
                confidence += 0.10
            confidence = min(confidence, 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=calc_sl_bps,
                take_profit_bps=calc_tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "liquidation_cascade_exhaustion_bottom",
                    "cascade_range_to_atr": round(prev_range / atr_series, 2),
                    "cascade_vol_mult": round(prev_vol / vol_sma_series, 2),
                    "rsi": round(current_rsi, 2),
                    "funding_rate": funding_rate,
                    "fear_greed": fear_greed,
                    "cascade_low": prev_low,
                    "entry_price": closes[-1],
                },
            )

        return None