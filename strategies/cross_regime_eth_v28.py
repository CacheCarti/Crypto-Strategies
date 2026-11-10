from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcCatchupEvent(Strategy):
    METADATA = {
        "name": "EthBtcCatchupEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.spread_threshold = 2.2
        self.bars_in_trade = 0
        self.max_hold_bars = 16

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Manage existing open position
        if ctx.has_position():
            self.bars_in_trade += 1
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_time_elapsed_exit",
                        "bars_held": self.bars_in_trade,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard to prevent rapid overtrading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Avoid extreme crisis / meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Setup 1: ETH severely lagging BTC (ETH underperforming by >= 2.2%),
        # expecting catchup expansion. Filter with RSI not completely broken.
        if spread <= -self.spread_threshold and rsi < 58.0:
            confidence = min(0.9, 0.55 + abs(spread) * 0.05)
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_event",
                    "spread": round(spread, 3),
                    "btc_return_pct": round(btc_ret, 3),
                    "eth_return_pct": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "regime": regime,
                },
            )

        # Setup 2: ETH stretched far ahead of BTC (ETH outperforming by >= 2.2%),
        # expecting spread mean reversion. Filter with RSI not fully oversold.
        if spread >= self.spread_threshold and rsi > 42.0:
            confidence = min(0.9, 0.55 + abs(spread) * 0.05)
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_stretched_over_btc_reversion",
                    "spread": round(spread, 3),
                    "btc_return_pct": round(btc_ret, 3),
                    "eth_return_pct": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "regime": regime,
                },
            )

        return None