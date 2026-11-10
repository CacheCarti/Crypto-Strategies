from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.max_hold_bars = 48

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        fg = ctx.features.get("fear_greed_index", 50.0)
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Normalization exits or time limit
            exit_reason = None
            if rsi >= 60.0:
                exit_reason = "rsi_normalized"
            elif funding > 0.00015:
                exit_reason = "funding_flipped_positive"
            elif bars_held >= self.max_hold_bars:
                exit_reason = "max_hold_exceeded"

            if exit_reason:
                self.last_exit_bar = ctx.bar_index
                self.entry_bar = -1
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": rsi,
                        "funding": funding,
                        "fg": fg,
                        "bars_held": bars_held
                    }
                )
            return None

        # Guard against post-exit rapid re-entry
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries in outright system collapse
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Triple-confluence stack: negative funding + market fear + technical oversold
        cond_funding = funding < 0.0
        cond_fear = fg <= 35.0
        cond_rsi = rsi <= 35.0

        if cond_funding and cond_fear and cond_rsi:
            self.entry_bar = ctx.bar_index
            # High conviction scaling if deeply oversold
            confidence = 0.75 if (rsi < 28.0 and funding < -0.0001) else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=650.0,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fg_rsi",
                    "rsi": rsi,
                    "funding_rate": funding,
                    "fear_greed": fg,
                    "regime": regime
                }
            )

        return None