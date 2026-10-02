from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolBtcCrossMomentum(Strategy):
    METADATA = {
        "name": "SolBtcCrossMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 25,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_ema = 14
        self.period_rsi = 14
        self.btc_long_thresh = 1.0
        self.btc_short_thresh = -1.0
        self.cooldown_bars = 3
        self.last_exit_bar = -100

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.period_ema)
        rsi = self._rsi(closes, self.period_rsi)

        if ema is None or rsi is None:
            return None

        # Handle both percentage (e.g. 1.5) and fractional (e.g. 0.015) representations
        raw_btc_ret = ctx.features.get("btc_return_pct", ctx.market.get("btc_return_pct", 0.0))
        btc_ret = raw_btc_ret * 100.0 if -0.2 < raw_btc_ret < 0.2 and raw_btc_ret != 0.0 else raw_btc_ret

        pos_dir = ctx.position_direction()

        # Exit management for open positions
        if ctx.has_position():
            if pos_dir == "long":
                # Exit when BTC momentum reverses or SOL hits extreme overbought exhaustion
                if btc_ret < 0.0 or rsi > 78.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_exit_btc_reversal_or_sol_exhaustion",
                            "btc_return_pct": btc_ret,
                            "rsi": rsi,
                            "price": current_price,
                            "ema": ema,
                        },
                    )
            elif pos_dir == "short":
                # Exit when BTC dumps reverse or SOL hits extreme oversold bounce
                if btc_ret > 0.0 or rsi < 22.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_exit_btc_reversal_or_sol_exhaustion",
                            "btc_return_pct": btc_ret,
                            "rsi": rsi,
                            "price": current_price,
                            "ema": ema,
                        },
                    )
            return None

        # Cooldown guard after exit to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Loosened, robust entry conditions: BTC directional lead + SOL trend confirmation
        long_cond = btc_ret >= self.btc_long_thresh and current_price > ema and rsi < 72.0
        short_cond = btc_ret <= self.btc_short_thresh and current_price < ema and rsi > 28.0

        if long_cond:
            conf = min(0.9, 0.6 + abs(btc_ret) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_expansion_sol_trend_long",
                    "btc_return_pct": btc_ret,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price,
                },
            )

        if short_cond:
            conf = min(0.9, 0.6 + abs(btc_ret) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_contraction_sol_trend_short",
                    "btc_return_pct": btc_ret,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price,
                },
            )

        return None