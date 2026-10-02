from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcVolExpansionMomentum(Strategy):
    METADATA = {
        "name": "BTC Volatility Expansion Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 75,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.baseline_period = 50
        self.momentum_bars = 12
        self.expansion_threshold = 1.50
        self.contraction_exit_threshold = 0.85
        self.momentum_atr_mult = 1.80
        self.cooldown_bars = 14
        self.last_exit_bar = -100

    def _calculate_atr_series(self, highs, lows, closes, total_needed):
        if len(closes) < self.atr_period + total_needed:
            return []
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)
        
        atr_series = []
        for i in range(self.atr_period, len(trs) + 1):
            atr_series.append(sum(trs[i - self.atr_period:i]) / self.atr_period)
        return atr_series

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.baseline_period + self.atr_period + self.momentum_bars + 10
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)

        if len(closes) < needed_bars:
            return None

        atr_series = self._calculate_atr_series(highs, lows, closes, self.baseline_period)
        if len(atr_series) < self.baseline_period:
            return None

        current_atr = atr_series[-1]
        baseline_atr = sum(atr_series[-self.baseline_period:]) / self.baseline_period
        if baseline_atr <= 0:
            return None

        atr_ratio = current_atr / baseline_atr
        current_price = closes[-1]
        past_price = closes[-1 - self.momentum_bars]
        price_momentum = current_price - past_price
        momentum_threshold = self.momentum_atr_mult * current_atr

        ema_trend = self._ema(closes, 50)

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # In-position management
        if has_pos:
            # 1. Exit when volatility contracts deeply back to calm/exhaustion
            if atr_ratio < self.contraction_exit_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "atr_contraction_exhaustion",
                        "atr_ratio": round(atr_ratio, 3),
                        "current_atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "price": current_price
                    }
                )

            # 2. Reversal exit if momentum vigorously reverses against current direction
            if pos_dir == "long" and price_momentum < -momentum_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_momentum_breakdown_reversal",
                        "price_momentum": round(price_momentum, 2),
                        "atr_ratio": round(atr_ratio, 3),
                        "price": current_price
                    }
                )
            elif pos_dir == "short" and price_momentum > momentum_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_momentum_breakout_reversal",
                        "price_momentum": round(price_momentum, 2),
                        "atr_ratio": round(atr_ratio, 3),
                        "price": current_price
                    }
                )
            return None

        # Entry logic: strict post-exit cooldown to avoid friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime & Crisis gating
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Only trigger entry on strong volatility expansion
        if atr_ratio >= self.expansion_threshold and ema_trend is not None:
            conf = min(0.90, 0.60 + (atr_ratio - self.expansion_threshold) * 0.3)

            # Bullish expansion: strong upward thrust + above baseline trend
            if price_momentum > momentum_threshold and current_price > ema_trend:
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bullish_thrust",
                        "atr_ratio": round(atr_ratio, 3),
                        "price_momentum": round(price_momentum, 2),
                        "momentum_threshold": round(momentum_threshold, 2),
                        "ema_50": round(ema_trend, 2),
                        "price": current_price
                    }
                )

            # Bearish expansion: strong downward thrust + below baseline trend
            if price_momentum < -momentum_threshold and current_price < ema_trend:
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bearish_thrust",
                        "atr_ratio": round(atr_ratio, 3),
                        "price_momentum": round(price_momentum, 2),
                        "momentum_threshold": round(momentum_threshold, 2),
                        "ema_50": round(ema_trend, 2),
                        "price": current_price
                    }
                )

        return None