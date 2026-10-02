from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcBetaCatchupScalp(Strategy):
    METADATA = {
        "name": "BTC Beta Catchup Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_history = []
        self.last_trade_bar = -100
        self.bars_in_pos = 0
        self.lookback = 6  # 30-minute delta window (6 * 5m)
        self.btc_thresh = 1.05  # minimum % move on BTC
        self.sol_lag_max = 0.45  # maximum % SOL has already moved
        self.cooldown_bars = 16  # minimum 80 min cooldown between trades

    def _rsi(self, closes, period=7):
        if len(closes) < period + 1:
            return 50.0
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
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_btc = ctx.features.get("btc_return_pct", 0.0)
        self.btc_history.append(current_btc)
        if len(self.btc_history) > 50:
            self.btc_history.pop(0)

        # Manage existing position horizon
        if ctx.has_position():
            self.bars_in_pos += 1
            if self.bars_in_pos >= 5:  # Flat after 25 mins if not hit SL/TP
                rsi = self._rsi(closes, 7)
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_decay_exit", "bars_held": self.bars_in_pos, "rsi": round(rsi, 2)}
                )
            return None
        else:
            self.bars_in_pos = 0

        # Enforce strict post-exit cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        if len(self.btc_history) < self.lookback + 1:
            return None

        # Measure recent impulse on BTC
        btc_prev = self.btc_history[-self.lookback - 1]
        btc_delta = current_btc - btc_prev

        # Measure SOL's move over the same window
        sol_now = closes[-1]
        sol_prev = closes[-self.lookback - 1]
        sol_ret = ((sol_now - sol_prev) / sol_prev) * 100.0

        rsi = self._rsi(closes, 7)

        # Bullish Catch-up: BTC surged up, SOL has lagged, RSI not yet overbought
        if btc_delta >= self.btc_thresh and sol_ret < self.sol_lag_max and rsi < 67.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.90, max(0.60, 0.65 + (btc_delta - self.btc_thresh) * 0.15))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_beta_catchup_long",
                    "btc_delta": round(btc_delta, 3),
                    "sol_ret": round(sol_ret, 3),
                    "rsi": round(rsi, 2),
                    "price": sol_now
                }
            )

        # Bearish Catch-up: BTC dumped, SOL has lagged, RSI not yet oversold
        if btc_delta <= -self.btc_thresh and sol_ret > -self.sol_lag_max and rsi > 33.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.90, max(0.60, 0.65 + (abs(btc_delta) - self.btc_thresh) * 0.15))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_beta_catchup_short",
                    "btc_delta": round(btc_delta, 3),
                    "sol_ret": round(sol_ret, 3),
                    "rsi": round(rsi, 2),
                    "price": sol_now
                }
            )

        return None