from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RelStrengthEthRotation(Strategy):
    METADATA = {
        "name": "RelStrengthEthRotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.reversion_threshold = 0.5
        self.cooldown_bars = 8
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.prev_spread = 0.0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        current_price = ctx.bar.close

        # Position exit management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            # Mean-reversion exit or max hold event boundary reached
            if direction == "long" and (spread <= self.reversion_threshold or bars_in_trade >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_spread_reverted_or_time_exit",
                        "spread": round(spread, 3),
                        "bars_held": bars_in_trade,
                        "price": current_price,
                    }
                )
            elif direction == "short" and (spread >= -self.reversion_threshold or bars_in_trade >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_spread_reverted_or_time_exit",
                        "spread": round(spread, 3),
                        "bars_held": bars_in_trade,
                        "price": current_price,
                    }
                )
            self.prev_spread = spread
            return None

        # Cooldown guard after any exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.prev_spread = spread
            return None

        # Event trigger: strict threshold crossover to capture fresh momentum impulse
        long_event = (self.prev_spread <= self.spread_threshold) and (spread > self.spread_threshold)
        short_event = (self.prev_spread >= -self.spread_threshold) and (spread < -self.spread_threshold)

        self.prev_spread = spread

        # Price confirmation filter: align with local trend to avoid false breakouts
        if long_event and (current_price >= ema20):
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + max(0.0, (spread - self.spread_threshold) * 0.1))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outperformance_event_cross",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema20": round(ema20, 2),
                    "price": current_price,
                }
            )

        if short_event and (current_price <= ema20):
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + max(0.0, (-spread - self.spread_threshold) * 0.1))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_underperformance_event_cross",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema20": round(ema20, 2),
                    "price": current_price,
                }
            )

        return None