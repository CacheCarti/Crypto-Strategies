from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCrossAssetMomentum(Strategy):
    METADATA = {
        "name": "SolBtcCrossAssetMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_long_thresh = 2.8
        self.btc_short_thresh = -2.8
        self.btc_exit_thresh = 1.0
        self.ema_period = 14
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        sol_ema = self._ema(closes, self.ema_period)
        sol_rsi = self._rsi(closes, self.rsi_period)

        if sol_ema is None or sol_rsi is None:
            return None

        current_close = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic for open positions
        if has_pos:
            if pos_dir == "long":
                if btc_ret < self.btc_exit_thresh or current_close < sol_ema * 0.996:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_momentum_flattened_or_ema_lost",
                            "btc_ret": btc_ret,
                            "sol_close": current_close,
                            "sol_ema": sol_ema,
                            "sol_rsi": sol_rsi,
                        },
                    )
            elif pos_dir == "short":
                if btc_ret > -self.btc_exit_thresh or current_close > sol_ema * 1.004:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_momentum_flattened_or_ema_regained",
                            "btc_ret": btc_ret,
                            "sol_close": current_close,
                            "sol_ema": sol_ema,
                            "sol_rsi": sol_rsi,
                        },
                    )
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic
        if btc_ret >= self.btc_long_thresh and current_close > sol_ema and sol_rsi >= 50.0:
            confidence = min(0.85, 0.55 + (btc_ret - self.btc_long_thresh) * 0.04)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_strong_bull_cross_momentum_sol_aligned",
                    "btc_return_pct": btc_ret,
                    "sol_ema": sol_ema,
                    "sol_rsi": sol_rsi,
                    "sol_close": current_close,
                },
            )

        if btc_ret <= self.btc_short_thresh and current_close < sol_ema and sol_rsi <= 50.0:
            confidence = min(0.85, 0.55 + abs(btc_ret - self.btc_short_thresh) * 0.04)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_strong_bear_cross_momentum_sol_aligned",
                    "btc_return_pct": btc_ret,
                    "sol_ema": sol_ema,
                    "sol_rsi": sol_rsi,
                    "sol_close": current_close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index