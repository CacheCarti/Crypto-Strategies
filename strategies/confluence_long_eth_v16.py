from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceSqueeze(Strategy):
    METADATA = {
        "name": "EthConfluenceSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # 8 hours intended swing
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 36  # ~36 hours max hold

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Handle active position management
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            # Exit triggers: RSI mean reverts to neutral/overbought, crowd begins paying positive funding, or max hold reached
            exit_reason = None
            if current_rsi >= 58.0:
                exit_reason = "rsi_normalized"
            elif funding > 0.00015:
                exit_reason = "crowd_crowded_long"
            elif bars_in_trade >= self.max_hold_bars:
                exit_reason = "max_time_elapsed"

            if exit_reason:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "fg_index": fg_index,
                        "bars_held": bars_in_trade,
                    }
                )
            return None

        # Cooldown guard after trade exit to prevent whipsaw overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis filter: avoid catching falling knives during complete market meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.75:
            return None

        # Strict Confluence Triad:
        # 1. Negative funding (crowd paying to short / crowded short bias)
        # 2. Macro Fear & Greed < 35 (prevailing extreme fear or fear)
        # 3. 1h RSI < 35 (local technical oversold condition)
        is_funding_short = funding < 0.0
        is_fear = fg_index < 35.0
        is_oversold = current_rsi < 35.0

        if is_funding_short and is_fear and is_oversold:
            # Scale confidence: higher if funding is deeply negative and fear is extreme
            funding_boost = min(abs(funding) * 1000.0, 0.2)
            fear_boost = max((35.0 - fg_index) / 70.0, 0.0)
            base_confidence = 0.65 + funding_boost + fear_boost
            confidence = min(round(base_confidence, 2), 0.95)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(current_rsi, 2),
                    "funding": round(funding, 6),
                    "fg_index": fg_index,
                    "crisis_score": round(crisis_score, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index