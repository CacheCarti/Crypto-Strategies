from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class SolBtcLagScalper(Strategy):
    METADATA = {
        "name": "SOL BTC Beta Lag Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 165.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 5
        self.cooldown_bars = 14
        self.btc_surge_threshold = 0.85
        self.sol_lag_threshold = 0.20
        self.max_hold_bars = 5
        
        self.btc_ret_history = deque(maxlen=30)
        self.entry_bar = -100
        self.last_exit_bar = -100

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        closes = ctx.closes(35)
        if len(closes) < 30 or ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append((ctx.bar_index, btc_ret))

        # Handle active position exit logic
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                rsi = self._rsi(closes, 14)
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_bars_scalp_horizon_reached",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "price": closes[-1],
                    }
                )
            return None

        # Check cooldown after previous trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(self.btc_ret_history) < self.lookback_bars + 1:
            return None

        # Calculate recent impulse on BTC and response on SOL
        past_btc_entry = self.btc_ret_history[- (self.lookback_bars + 1)]
        btc_delta = btc_ret - past_btc_entry[1]

        sol_start_price = closes[- (self.lookback_bars + 1)]
        sol_current_price = closes[-1]
        sol_ret = ((sol_current_price - sol_start_price) / sol_start_price) * 100.0

        rsi = self._rsi(closes, 14)

        # Long Setup: BTC surges up sharply, but SOL is lagging behind
        if btc_delta >= self.btc_surge_threshold and sol_ret <= self.sol_lag_threshold and rsi < 66.0:
            gap = btc_delta - sol_ret
            conf = min(0.90, max(0.60, 0.60 + (gap * 0.15)))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_surge_sol_lag_long",
                    "btc_delta": round(btc_delta, 3),
                    "sol_ret": round(sol_ret, 3),
                    "lag_spread": round(gap, 3),
                    "rsi": round(rsi, 2),
                    "price": sol_current_price,
                }
            )

        # Short Setup: BTC drops sharply, but SOL hasn't dumped yet
        if btc_delta <= -self.btc_surge_threshold and sol_ret >= -self.sol_lag_threshold and rsi > 34.0:
            gap = abs(btc_delta - sol_ret)
            conf = min(0.90, max(0.60, 0.60 + (gap * 0.15)))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_dump_sol_lag_short",
                    "btc_delta": round(btc_delta, 3),
                    "sol_ret": round(sol_ret, 3),
                    "lag_spread": round(gap, 3),
                    "rsi": round(rsi, 2),
                    "price": sol_current_price,
                }
            )

        return None