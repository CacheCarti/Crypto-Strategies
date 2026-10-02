from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBetaFollower(Strategy):
    METADATA = {
        "name": "SolBetaFollower",
        "domain": "sol_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 21
        self.rsi_period = 14
        self.eth_thresh_long = 1.75
        self.eth_thresh_short = -1.75
        self.sol_confirm_thresh = 0.80
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.prev_eth_ret = 0.0

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 15)
        if len(closes) < self.ema_period + 10:
            return None

        sol_ema = self._ema(closes, self.ema_period)
        sol_rsi = self._rsi(closes, self.rsi_period)
        if sol_ema is None or sol_rsi is None:
            return None

        current_close = ctx.bar.close
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)

        eth_momentum_up = eth_ret > self.prev_eth_ret
        eth_momentum_down = eth_ret < self.prev_eth_ret
        self.prev_eth_ret = eth_ret

        # Position management and exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit long if ETH driver flattens, reverses sharply, or RSI is overextended
                if eth_ret <= 0.25 or (not eth_momentum_up and eth_ret < 1.0) or sol_rsi > 78.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "eth_momentum_flattened_long_exit",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "sol_rsi": round(sol_rsi, 2),
                            "close": current_close,
                        },
                    )
            elif pos_dir == "short":
                # Exit short if ETH driver flattens, reverses sharply, or RSI is oversold
                if eth_ret >= -0.25 or (not eth_momentum_down and eth_ret > -1.0) or sol_rsi < 22.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "eth_momentum_flattened_short_exit",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "sol_rsi": round(sol_rsi, 2),
                            "close": current_close,
                        },
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long entry setup: strong positive ETH move rising, confirmed by SOL positive move & price above EMA
        if (
            eth_ret >= self.eth_thresh_long
            and eth_momentum_up
            and sol_ret >= self.sol_confirm_thresh
            and current_close > sol_ema
            and sol_rsi < 70.0
        ):
            confidence = min(0.9, 0.55 + (eth_ret / 15.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_beta_expansion_long",
                    "eth_return_pct": eth_ret,
                    "sol_return_pct": sol_ret,
                    "sol_ema21": round(sol_ema, 2),
                    "sol_rsi": round(sol_rsi, 2),
                    "close": current_close,
                },
            )

        # Short entry setup: strong negative ETH move falling, confirmed by SOL negative move & price below EMA
        if (
            eth_ret <= self.eth_thresh_short
            and eth_momentum_down
            and sol_ret <= -self.sol_confirm_thresh
            and current_close < sol_ema
            and sol_rsi > 30.0
        ):
            confidence = min(0.9, 0.55 + (abs(eth_ret) / 15.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_beta_expansion_short",
                    "eth_return_pct": eth_ret,
                    "sol_return_pct": sol_ret,
                    "sol_ema21": round(sol_ema, 2),
                    "sol_rsi": round(sol_rsi, 2),
                    "close": current_close,
                },
            )

        return None