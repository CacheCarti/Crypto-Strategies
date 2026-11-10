from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversal(Strategy):
    METADATA = {
        "name": "EthConfluenceReversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8 hour target swing hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 24  # Force flat after 24 hours if thesis has stalled

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        current_price = ctx.bar.close

        # Position Management & Normalization Exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Normalize exit: RSI bounced back above neutral or funding rate normalized positive with strong RSI
            rsi_normalized = rsi >= 52.0
            crowd_realigned = (funding > 0.0001) and (rsi >= 45.0)
            timeout = bars_held >= self.max_hold_bars

            if rsi_normalized or crowd_realigned or timeout:
                reason = "rsi_normalized" if rsi_normalized else ("crowd_realigned" if crowd_realigned else "time_limit")
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": f"exit_{reason}",
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                        "price": current_price
                    }
                )
            return None

        # Cooldown guard: prevent overtrading and churning friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check triple confluence:
        # 1. Technical: RSI oversold or turning up from deep oversold (< 38.0 to avoid being brittle)
        # 2. Market Sentiment: Fear & Greed indicates fear regime (<= 40.0)
        # 3. Market Structure: Negative funding rate indicating crowded short / discounted perps (<= 0.0)
        is_rsi_oversold = rsi <= 38.0
        is_fear_regime = fg_index <= 40.0
        is_crowd_short = funding <= 0.00002  # Neutral-to-negative funding

        if is_rsi_oversold and is_fear_regime and is_crowd_short:
            # Scaled confidence based on depth of extremes
            rsi_score = max(0.0, (40.0 - rsi) / 20.0)
            fg_score = max(0.0, (40.0 - fg_index) / 30.0)
            funding_score = 0.3 if funding < 0.0 else 0.1
            confidence = min(0.95, max(0.55, 0.4 + (rsi_score * 0.3) + (fg_score * 0.2) + funding_score))

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_oversold_crowd_short",
                    "rsi": round(rsi, 2),
                    "fear_greed": round(fg_index, 1),
                    "funding_rate": round(funding, 6),
                    "price": current_price
                }
            )

        return None