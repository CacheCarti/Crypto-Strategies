from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingSqueezeContrarian(Strategy):
    METADATA = {
        "name": "EthFundingSqueezeContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 18

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Track funding rate feature history
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Technical context
        price = ctx.bar.close
        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")

        # POSITION MANAGEMENT / EXIT
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Normalization exit: funding reverts back toward neutrality or time horizon reached
            normalized_funding = abs(current_funding) <= 0.00003
            time_exit = bars_held >= self.max_hold_bars

            # Tactical adverse momentum stop
            adverse_rsi = (pos_dir == "long" and rsi < 28.0) or (pos_dir == "short" and rsi > 72.0)

            if normalized_funding or time_exit or adverse_rsi:
                reason = "funding_normalized" if normalized_funding else ("time_max_hold" if time_exit else "adverse_momentum")
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "funding": current_funding,
                        "rsi": round(rsi, 2),
                        "price": price,
                    }
                )
            return None

        # COOLDOWN & CRISIS FILTER
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Need at least 3 consecutive persistent funding reads to establish crowded positioning
        if len(self.funding_history) < 3:
            return None
        recent_3 = self.funding_history[-3:]

        # EVENT BOUNDARY: Squeeze triggers with momentum turn filter
        # Negative funding: crowd is short, paying carry. Look for local bullish absorption.
        crowded_short = all(f <= -0.00010 for f in recent_3)
        if crowded_short:
            # Entry event: price stabilizes (not falling knife) and RSI recovers above oversold boundary
            price_above_support = price >= ema20 * 0.985
            momentum_turn = 32.0 <= rsi <= 55.0
            if price_above_support and momentum_turn:
                self.entry_bar = ctx.bar_index
                conf = min(0.85, 0.55 + abs(current_funding) * 1500)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=320.0,
                    take_profit_bps=520.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "crowded_short_funding_squeeze",
                        "avg_funding_3": sum(recent_3) / 3.0,
                        "funding": current_funding,
                        "rsi": round(rsi, 2),
                        "ema20": round(ema20, 2),
                        "price": price,
                    }
                )

        # Positive funding: crowd is long, paying carry. Look for exhaustion / rejection.
        crowded_long = all(f >= 0.00018 for f in recent_3)
        if crowded_long:
            # Entry event: price is capped below extended runaway and RSI rolls over
            price_below_resistance = price <= ema20 * 1.015
            momentum_fade = 45.0 <= rsi <= 68.0
            if price_below_resistance and momentum_fade:
                self.entry_bar = ctx.bar_index
                conf = min(0.85, 0.55 + current_funding * 1200)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=320.0,
                    take_profit_bps=520.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "crowded_long_funding_fade",
                        "avg_funding_3": sum(recent_3) / 3.0,
                        "funding": current_funding,
                        "rsi": round(rsi, 2),
                        "ema20": round(ema20, 2),
                        "price": price,
                    }
                )

        return None