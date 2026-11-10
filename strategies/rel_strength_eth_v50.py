from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentumBreakout(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentumBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2.0% return spread differential
        self.cooldown_bars = 6       # Wait 6 bars (6 hours) after position close
        self.last_close_bar = -100
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
        self.last_close_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Feature reads with defaults
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        current_spread = eth_ret - btc_ret

        # If holding a position, manage mean-reversion exit
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion of spread triggers flat exit
            if direction == "long" and current_spread <= 0.0:
                self.prev_spread = current_spread
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_reverted_neutral_exit",
                        "spread": round(current_spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                    }
                )
            elif direction == "short" and current_spread >= 0.0:
                self.prev_spread = current_spread
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_reverted_neutral_exit",
                        "spread": round(current_spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                    }
                )
            self.prev_spread = current_spread
            return None

        # Check cooldown to prevent overtrading and fee drag
        if ctx.bar_index - self.last_close_bar < self.cooldown_bars:
            self.prev_spread = current_spread
            return None

        # Check crisis filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            self.prev_spread = current_spread
            return None

        # Trend filter on ETH to avoid fighting structural direction
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            self.prev_spread = current_spread
            return None

        price = ctx.bar.close

        # Strict event-boundary triggers: CROSSOVER of the spread threshold
        long_cross = (self.prev_spread <= self.spread_threshold) and (current_spread > self.spread_threshold)
        short_cross = (self.prev_spread >= -self.spread_threshold) and (current_spread < -self.spread_threshold)

        self.prev_spread = current_spread

        # Long: ETH breaks out against BTC + price is above EMA20 (trend confirmation)
        if long_cross and price > ema20:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_breakout_cross",
                    "spread": round(current_spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "price": price,
                    "ema20": round(ema20, 2),
                }
            )

        # Short: ETH breaks down against BTC + price is below EMA20
        if short_cross and price < ema20:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_breakdown_cross",
                    "spread": round(current_spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "price": price,
                    "ema20": round(ema20, 2),
                }
            )

        return None