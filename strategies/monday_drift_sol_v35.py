from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondayDriftSeasonality(Strategy):
    METADATA = {
        "name": "SolMondayDriftSeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.max_hold_bars = 24

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        rsi = self._rsi(closes, 14)
        sma20 = self._sma(closes, 20)
        sma50 = self._sma(closes, 50)

        if rsi is None or sma20 is None or sma50 is None:
            return None

        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")

        has_pos = ctx.has_position()

        # Exit logic
        if has_pos:
            bars_held = ctx.bar_index - self.last_entry_bar
            # Exit window: Monday after 21:00 UTC or Tuesday anytime, or held too long
            exit_by_time = (weekday == 0 and hour >= 21) or (weekday == 1) or (bars_held >= self.max_hold_bars)
            # Technical breakdown exit: price lost SMA20 and RSI rolling over below 42
            exit_by_trend = closes[-1] < sma20 and rsi < 42.0

            if exit_by_time or exit_by_trend:
                reason = "monday_session_end" if exit_by_time else "trend_breakdown_exit"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "close": closes[-1]
                    }
                )
            return None

        # Entry logic: Only enter when flat
        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Hard guard to avoid crisis trading
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Sunday late UTC (18:00 - 23:00) entry window
        is_sunday_entry_window = (weekday == 6 and hour >= 18)

        if is_sunday_entry_window:
            # Avoid long-crowded funding spikes, require non-overbought pullback (RSI between 32 and 60)
            healthy_rsi = 32.0 <= rsi <= 60.0
            not_overheated_funding = funding_rate < 0.0003
            not_extreme_panic = fear_greed >= 15

            # Trend momentum confirmation: recent close not collapsing well below 50-SMA
            trend_sound = closes[-1] >= sma50 * 0.98

            if healthy_rsi and not_overheated_funding and not_extreme_panic and trend_sound:
                # Dynamic confidence scaling
                confidence = 0.65
                if closes[-1] > sma20:
                    confidence += 0.1
                if funding_rate <= 0.0:  # Negative/flat funding indicates lack of long crowding
                    confidence += 0.1
                confidence = min(0.9, confidence)

                self.last_entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=350.0,
                    take_profit_bps=500.0,
                    horizon_seconds=86400,
                    metadata={
                        "reason": "sunday_pre_monday_seasonal_drift",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "funding_rate": round(funding_rate, 6),
                        "fear_greed": fear_greed,
                        "sma20": round(sma20, 2),
                        "sma50": round(sma50, 2),
                    }
                )

        return None