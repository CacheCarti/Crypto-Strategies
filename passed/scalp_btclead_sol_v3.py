from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaScalp(Strategy):
    METADATA = {
        "name": "SOL Cross-Asset Beta Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 165.0,
        "declared_hold_seconds": 1500,  # 5 bars (25 mins)
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.impulse_lookback = 4
        self.btc_impulse_threshold = 0.90  # 0.90% delta in BTC 24h return over 20 mins
        self.sol_max_reaction = 0.35       # SOL has moved less than 0.35% (lagging)
        self.last_exit_bar = -50
        self.entry_bar = 0
        self.btc_history = []

    def _rsi(self, closes, period=7):
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track BTC return stream
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_history.append(btc_ret)
        if len(self.btc_history) > 20:
            self.btc_history.pop(0)

        # In-position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_horizon_exit",
                        "bars_held": bars_held,
                        "sol_price": ctx.bar.close
                    }
                )
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(self.btc_history) <= self.impulse_lookback:
            return None

        # Calculate BTC velocity & SOL reaction
        btc_impulse = self.btc_history[-1] - self.btc_history[-1 - self.impulse_lookback]
        sol_ret_pct = (closes[-1] - closes[-1 - self.impulse_lookback]) / closes[-1 - self.impulse_lookback] * 100.0
        rsi = self._rsi(closes, period=7)

        # Long Catch-up Setup: BTC surging upward, SOL hasn't moved up yet, RSI not exhausted
        if btc_impulse >= self.btc_impulse_threshold and sol_ret_pct < self.sol_max_reaction and rsi < 65.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.90, 0.55 + abs(btc_impulse) * 0.15)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_beta_lag_long",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret_pct": round(sol_ret_pct, 3),
                    "rsi": round(rsi, 2),
                    "sol_price": ctx.bar.close
                }
            )

        # Short Catch-up Setup: BTC dumping, SOL hasn't dropped yet, RSI not oversold
        if btc_impulse <= -self.btc_impulse_threshold and sol_ret_pct > -self.sol_max_reaction and rsi > 35.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.90, 0.55 + abs(btc_impulse) * 0.15)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_beta_lag_short",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret_pct": round(sol_ret_pct, 3),
                    "rsi": round(rsi, 2),
                    "sol_price": ctx.bar.close
                }
            )

        return None