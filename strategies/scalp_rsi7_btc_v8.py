from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcRsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BTC RSI Snapback Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 130.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 25,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 8
        self.oversold = 22.0
        self.overbought = 78.0
        self.neutral_low = 45.0
        self.neutral_high = 55.0
        self.cooldown_bars = 8
        self.max_hold_bars = 6
        self.last_trade_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period):
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        bar_