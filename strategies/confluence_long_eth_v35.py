from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversion(Strategy):
    METADATA = {
        "name": "EthConfluenceReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 36

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

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # In-position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Exit when conditions normalize or max hold reached
            rsi_normalized = current_rsi >= 52.0
            sentiment_normalized = fg_index >= 45.0
            funding_normalized = funding > 0.00015
            timed_out = bars_held >= self.max_hold_bars

            if rsi_normalized or sentiment_normalized or funding_normalized or timed_out:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalization_or_time",
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "fg_index": round(fg_index, 1),
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard after last exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market stress guard - avoid entering during systemic collapse
        if ctx.regime == "crisis":
            return None

        # Cross-asset stability confirmation: avoid catching knives if BTC is dumping hard (> -4% in 24h)
        btc_filter = btc_ret > -4.0

        # Core Triple-Stack Confluence:
        # 1. Crowd over-positioned short (negative funding)
        # 2. Macro sentiment fear zone (fear & greed index <= 35)
        # 3. Technical oversold state (RSI < 36)
        confluence_trigger = (
            funding < 0.0
            and fg_index <= 35.0
            and current_rsi < 36.0
            and btc_filter
        )

        if confluence_trigger:
            self.entry_bar = ctx.bar_index
            # Higher conviction if fear or oversold is severe
            conviction = 0.65
            if current_rsi < 28.0:
                conviction += 0.15
            if funding < -0.0001:
                conviction += 0.10
            confidence = min(round(conviction, 2), 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "triple_stack_confluence_oversold_funding_fear",
                    "rsi": round(current_rsi, 2),
                    "funding_rate": round(funding, 6),
                    "fear_greed": round(fg_index, 1),
                    "btc_24h_ret": round(btc_ret, 2),
                    "price": ctx.bar.close
                }
            )

        return None