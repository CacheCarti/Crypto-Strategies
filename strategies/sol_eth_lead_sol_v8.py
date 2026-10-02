from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBetaFollowStrategy(Strategy):
    METADATA = {
        "name": "SolBetaFollower",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.rsi_period = 14
        self.eth_threshold_long = 1.85
        self.eth_threshold_short = -1.85
        self.sol_confirm_long = 0.75
        self.sol_confirm_short = -0.75
        self.eth_exit_long = 0.35
        self.eth_exit_short = -0.35
        self.cooldown_bars = 4
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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema is None or rsi is None:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)
        pos_dir = ctx.position_direction()

        # Exit logic for open positions
        if pos_dir == "long":
            if eth_ret <= self.eth_exit_long or rsi > 78.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_momentum_flattened_or_rsi_overbought",
                        "eth_return_pct": eth_ret,
                        "sol_return_pct": sol_ret,
                        "rsi": rsi,
                        "price": current_close,
                        "ema20": ema,
                    },
                )
            return None

        elif pos_dir == "short":
            if eth_ret >= self.eth_exit_short or rsi < 22.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_momentum_flattened_or_rsi_oversold",
                        "eth_return_pct": eth_ret,
                        "sol_return_pct": sol_ret,
                        "rsi": rsi,
                        "price": current_close,
                        "ema20": ema,
                    },
                )
            return None

        # Check cooldown before opening new positions
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: Strong ETH 24h lead + SOL confirmed positive + price > EMA20 + healthy RSI
        if eth_ret >= self.eth_threshold_long and sol_ret >= self.sol_confirm_long:
            if current_close > ema and 42.0 <= rsi <= 72.0:
                confidence = min(0.9, 0.55 + (eth_ret / 15.0))
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_beta_breakout_long",
                        "eth_return_pct": eth_ret,
                        "sol_return_pct": sol_ret,
                        "rsi": rsi,
                        "price": current_close,
                        "ema20": ema,
                    },
                )

        # Short Entry: Strong ETH 24h drop + SOL confirmed negative + price < EMA20 + healthy RSI
        elif eth_ret <= self.eth_threshold_short and sol_ret <= self.sol_confirm_short:
            if current_close < ema and 28.0 <= rsi <= 58.0:
                confidence = min(0.9, 0.55 + (abs(eth_ret) / 15.0))
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_beta_breakdown_short",
                        "eth_return_pct": eth_ret,
                        "sol_return_pct": sol_ret,
                        "rsi": rsi,
                        "price": current_close,
                        "ema20": ema,
                    },
                )

        return None