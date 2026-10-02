from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolCrossAssetBetaMomentum(Strategy):
    METADATA = {
        "name": "SOL Cross-Asset Beta Momentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 8
        self.slow_ema_period = 21
        self.rsi_period = 14
        self.btc_long_threshold = 2.8
        self.btc_short_threshold = -2.8
        self.btc_exit_long = 1.0
        self.btc_exit_short = -1.0
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Position Management & Mean Reversion / Momentum Fade Exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and btc_ret < self.btc_exit_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "btc_momentum_flattened_exit_long",
                        "btc_return_pct": btc_ret,
                        "sol_rsi": round(rsi, 2),
                        "sol_price": current_price,
                    },
                )
            elif pos_dir == "short" and btc_ret > self.btc_exit_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "btc_momentum_flattened_exit_short",
                        "btc_return_pct": btc_ret,
                        "sol_rsi": round(rsi, 2),
                        "sol_price": current_price,
                    },
                )
            return None

        # Mandatory Cooldown Guard
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Long Entry: Strong positive BTC surge + SOL local uptrend confirmation
        if btc_ret >= self.btc_long_threshold and ema_fast > ema_slow and rsi > 52.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.85, 0.55 + (btc_ret - self.btc_long_threshold) * 0.04 + (rsi - 50.0) * 0.005)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "cross_asset_btc_surge_sol_trend_long",
                    "btc_return_pct": btc_ret,
                    "sol_ema_fast": round(ema_fast, 3),
                    "sol_ema_slow": round(ema_slow, 3),
                    "sol_rsi": round(rsi, 2),
                    "sol_price": current_price,
                },
            )

        # Short Entry: Strong negative BTC dump + SOL local downtrend confirmation
        if btc_ret <= self.btc_short_threshold and ema_fast < ema_slow and rsi < 48.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(btc_ret - self.btc_short_threshold) * 0.04 + (50.0 - rsi) * 0.005)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "cross_asset_btc_dump_sol_trend_short",
                    "btc_return_pct": btc_ret,
                    "sol_ema_fast": round(ema_fast, 3),
                    "sol_ema_slow": round(ema_slow, 3),
                    "sol_rsi": round(rsi, 2),
                    "sol_price": current_price,
                },
            )

        return None