from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthEvent(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Event Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,  # 4-hour target hold on 1h bars
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2.0% divergence event trigger
        self.reversion_threshold = 0.5  # Mean reversion exit boundary
        self.cooldown_period = 6  # Minimum 6 bars (6 hours) between trades
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.max_hold_bars = 16

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Fetch returns safely
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Regime & Crisis Filter (avoid entering in systemic meltdown)
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None
        price = ctx.bar.close

        # Position Management
        if ctx.has_position():
            self.bars_in_trade += 1
            direction = ctx.position_direction()

            # Exit Condition 1: Event completed (relative spread mean-reverted)
            if direction == "long" and spread <= self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_spread_reverted", "spread": spread, "bars_held": self.bars_in_trade
                })
            elif direction == "short" and spread >= -self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_spread_reverted", "spread": spread, "bars_held": self.bars_in_trade
                })

            # Exit Condition 2: Max time-horizon boundary
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_stop_event_expired", "spread": spread, "bars_held": self.bars_in_trade
                })

            return None

        # Mandatory Post-Exit Cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Event Trigger Conditions:
        # Long: ETH outpacing BTC by >2% AND technical alignment (price above EMA20 confirming momentum)
        if spread >= self.spread_threshold and price > ema20:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_divergence_breakout",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "price": price,
                    "ema20": ema20
                }
            )

        # Short: ETH lagging BTC by <-2% AND technical breakdown (price below EMA20 confirming drag)
        if spread <= -self.spread_threshold and price < ema20:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_divergence_breakdown",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "price": price,
                    "ema20": ema20
                }
            )

        return None