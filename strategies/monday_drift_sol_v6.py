from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolWeekdaySeasonalityMomentum(Strategy):
    METADATA = {
        "name": "SolWeekdaySeasonalityMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 340.0,
        "declared_tp_bps": 580.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 21
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.last_entry_bar = -100
        self.last_exit_bar = -100
        self.entry_bar_index = 0

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # 0=Monday, 6=Sunday
        hour = ts.hour

        ema21 = self._ema(closes, self.ema_period)
        rsi14 = self._rsi(closes, self.rsi_period)

        if ema21 is None or rsi14 is None:
            return None

        has_pos = ctx.has_position()
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar

        # Check platform regime & sentiment filters
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # ------------------- EXIT LOGIC -------------------
        if has_pos:
            hold_bars = ctx.bar_index - self.entry_bar_index
            
            # Exit condition 1: Tuesday rollover (seasonality window expired)
            is_tuesday = weekday == 1
            is_late_monday = (weekday == 0 and hour >= 22)
            
            if is_late_monday or is_tuesday:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "seasonality_window_expired_monday_night",
                        "hold_bars": hold_bars,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi14": round(rsi14, 2),
                        "price": current_price,
                    },
                )

            # Exit condition 2: Overbought momentum exhaustion
            if rsi14 >= 78.0 and hold_bars >= 6:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "rsi_overbought_exhaustion_exit",
                        "hold_bars": hold_bars,
                        "rsi14": round(rsi14, 2),
                        "price": current_price,
                    },
                )

            # Exit condition 3: Emergency market breakdown
            if market_regime == "MELTDOWN" or crisis_score > 0.80:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.95,
                    metadata={
                        "reason": "crisis_meltdown_risk_off",
                        "crisis_score": round(crisis_score, 2),
                        "market_regime": market_regime,
                        "price": current_price,
                    },
                )

            return None

        # ------------------- ENTRY LOGIC -------------------
        # Enforce cooldown between trades
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Seasonality Entry Window: Sunday 18:00 UTC through Monday 06:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 6)
        seasonality_window = is_sunday_evening or is_monday_early

        if not seasonality_window:
            return None

        # Regime protection: skip entries in extreme market panics
        if market_regime == "MELTDOWN" or crisis_score > 0.65:
            return None

        # Price & momentum verification:
        # 1. RSI is not severely overbought (must have room to run) and not in freefall
        rsi_valid = 32.0 <= rsi14 <= 66.0
        # 2. Short-term price stabilization: close above EMA21 or closing higher than 2 bars ago
        price_recovering = current_price >= (ema21 * 0.992) or current_price > closes[-3]

        if rsi_valid and price_recovering:
            # Scale confidence: boost if funding is negative (short squeeze potential) or neutral sentiment
            confidence = 0.65
            if funding_rate < 0.0:
                confidence += 0.15
            if fg_index <= 45.0:
                confidence += 0.10
            confidence = min(1.0, max(0.50, confidence))

            self.last_entry_bar = ctx.bar_index
            self.entry_bar_index = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_seasonality_momentum_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi14": round(rsi14, 2),
                    "ema21": round(ema21, 2),
                    "funding_rate": round(funding_rate, 6),
                    "fg_index": fg_index,
                    "price": current_price,
                },
            )

        return None