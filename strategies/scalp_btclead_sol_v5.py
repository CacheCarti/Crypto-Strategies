from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcBetaCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcBetaCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 4             # 20 minutes (4 x 5m bars)
        self.btc_delta_thresh = 0.55  # 55 bps BTC impulse
        self.sol_lag_max = 0.20       # SOL max move allowed (20 bps) to qualify as lagging
        self.rsi_period = 7
        self.max_hold_bars = 6        # 30 minutes max hold
        self.cooldown_bars = 10       # 50 minutes post-exit cooldown
        
        self.btc_ret_history = []
        self.bars_in_position = 0
        self.last_exit_bar = -100

    def _rsi(self, closes, period=7):
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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track BTC 24h return series to detect sudden rate of change
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(btc_ret)
        if len(self.btc_ret_history) > 30:
            self.btc_ret_history.pop(0)

        # Position management
        if ctx.has_position():
            self.bars_in_position += 1
            pos_dir = ctx.position_direction()
            rsi = self._rsi(closes, self.rsi_period) or 50.0

            # Time-based expiration or momentum exhaustion exit
            if self.bars_in_position >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "max_hold_horizon_reached",
                        "bars_held": self.bars_in_position,
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                    }
                )

            if pos_dir == "long" and rsi >= 70.0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "long_rsi_overbought_exit",
                        "bars_held": self.bars_in_position,
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                    }
                )

            if pos_dir == "short" and rsi <= 30.0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "short_rsi_oversold_exit",
                        "bars_held": self.bars_in_position,
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                    }
                )

            return None

        # Reset bar counter when out of position
        self.bars_in_position = 0

        # Cooldown guard to avoid churn and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip during extreme crisis market regimes
        if ctx.regime == "crisis" or ctx.market.get("regime") == "MELTDOWN":
            return None

        if len(self.btc_ret_history) <= self.lookback:
            return None

        # Compute multi-bar BTC impulse vs SOL movement
        btc_impulse = self.btc_ret_history[-1] - self.btc_ret_history[-1 - self.lookback]
        sol_return_pct = ((closes[-1] - closes[-1 - self.lookback]) / closes[-1 - self.lookback]) * 100.0
        rsi = self._rsi(closes, self.rsi_period)

        if rsi is None:
            return None

        # Long Setup: BTC surges, SOL has lagged behind, and SOL is not already overbought
        if btc_impulse >= self.btc_delta_thresh and sol_return_pct <= self.sol_lag_max and rsi < 62.0:
            confidence = min(0.95, max(0.55, 0.55 + (btc_impulse - self.btc_delta_thresh) * 0.4))
            return ctx.signal(
                "long",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_lag_btc_bullish_impulse",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_return": round(sol_return_pct, 3),
                    "rsi": round(rsi, 2),
                    "price": ctx.bar.close,
                }
            )

        # Short Setup: BTC drops sharply, SOL has lagged down, and SOL is not already oversold
        if btc_impulse <= -self.btc_delta_thresh and sol_return_pct >= -self.sol_lag_max and rsi > 38.0:
            confidence = min(0.95, max(0.55, 0.55 + (abs(btc_impulse) - self.btc_delta_thresh) * 0.4))
            return ctx.signal(
                "short",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_lag_btc_bearish_impulse",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_return": round(sol_return_pct, 3),
                    "rsi": round(rsi, 2),
                    "price": ctx.bar.close,
                }
            )

        return None