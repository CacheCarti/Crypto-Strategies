from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 20
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 10)
        if len(closes) < self.trend_period + 1:
            return None

        bar_idx = ctx.bar_index
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        price = ctx.bar.close

        # Compute price-based filters
        sma50 = self._sma(closes, self.trend_period)
        rsi = self._rsi(closes, self.rsi_period)
        if sma50 is None or rsi is None:
            return None

        # Position Management & Exit Logic
        if ctx.has_position():
            # Mandatory weekend close: Sunday late evening (20:00 - 23:00 UTC)
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = bar_idx
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "sunday_scheduled_exit", "hour": hour, "rsi": rsi, "price": price}
                )

            # Defensive exit: Monday morning spillover fallback
            if weekday == 0 and hour >= 0:
                self.last_exit_bar = bar_idx
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "monday_overstay_exit", "hour": hour, "rsi": rsi, "price": price}
                )

            # Emergency technical exit: severe trend break or deep overbought condition
            bars_held = bar_idx - self.entry_bar
            if bars_held >= 6 and (rsi > 78.0 or price < sma50 * 0.96):
                self.last_exit_bar = bar_idx
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={"reason": "technical_risk_exit", "rsi": rsi, "sma50": sma50, "price": price}
                )

            return None

        # Entry Eligibility & Cooldown
        if (bar_idx - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: reject active crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Primary Seasonal Entry Window: Friday 18:00 - 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)

        if is_friday_entry_window:
            # High-conviction technical gating to avoid OOS rejects:
            # 1. Price above or consolidating near SMA50 (not in severe collapse)
            # 2. RSI reasonable (not extremely overbought, not in waterfall)
            if price >= sma50 * 0.985 and 38.0 <= rsi <= 66.0:
                self.entry_bar = bar_idx
                confidence = 0.75 if price > sma50 else 0.60
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=500.0,
                    take_profit_bps=750.0,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "friday_seasonality_long",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "sma50": round(sma50, 2),
                        "price": round(price, 2),
                        "regime": regime
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index