from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversal(Strategy):
    METADATA = {
        "name": "EthConfluenceReversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical hold
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 12          # 12-hour cooldown post-exit to enforce event isolation
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 36          # 36 hours maximum event study window

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
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Handle active position exit conditions
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Normalization exits: RSI recovered over neutral 50, funding turned strongly positive, or time limit hit
            exit_triggered = False
            exit_reason = ""
            if rsi >= 50.0:
                exit_triggered = True
                exit_reason = "rsi_normalized_above_50"
            elif funding > 0.0001:
                exit_triggered = True
                exit_reason = "funding_flipped_positive"
            elif bars_held >= self.max_hold_bars:
                exit_triggered = True
                exit_reason = "max_hold_horizon_reached"

            if exit_triggered:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                        "fg_index": fg_index
                    }
                )
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry Event: Triple-stack edge
        # 1. Crowd shorting bias (funding negative or neutral-flat)
        # 2. Market-wide fear sentiment
        # 3. Local oversold momentum
        if funding <= 0.0 and fg_index <= 35.0 and rsi <= 35.0:
            # Scale confidence by confluence depth
            conf = 0.65
            if rsi < 28.0:
                conf += 0.15
            if funding < -0.00005:
                conf += 0.10
            conf = min(conf, 0.95)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fg_rsi",
                    "rsi": round(rsi, 2),
                    "funding_rate": round(funding, 6),
                    "fear_greed": fg_index,
                    "price": ctx.bar.close
                }
            )

        return None