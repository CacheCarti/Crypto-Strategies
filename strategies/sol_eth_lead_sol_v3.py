from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolEthBetaFollow(Strategy):
    METADATA = {
        "name": "SOL Beta Momentum Follower",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -50
        self.eth_threshold_long = 2.2
        self.eth_threshold_short = -2.2
        self.sol_confirm_thresh = 1.2
        self.exit_thresh_abs = 0.5
        self.prev_eth_ret = 0.0

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
        if len(closes) < 35:
            return None

        eth_ret = float(ctx.features.get("eth_return_pct", 0.0))
        sol_ret = float(ctx.features.get("sol_return_pct", 0.0))
        prev_eth = self.prev_eth_ret
        self.prev_eth_ret = eth_ret

        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        current_close = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Manage existing position exits
        if has_pos:
            if pos_dir == "long":
                # Exit when ETH driver loses strength or SOL momentum reverses
                if eth_ret < self.exit_thresh_abs or eth_ret < (prev_eth - 1.2) or sol_ret < -0.3 or rsi > 78.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_beta_flatten_or_sol_divergence",
                            "eth_ret": round(eth_ret, 3),
                            "sol_ret": round(sol_ret, 3),
                            "rsi": round(rsi, 2),
                            "close": round(current_close, 3),
                        },
                    )
            elif pos_dir == "short":
                # Exit when ETH selling exhausts or SOL momentum bounces
                if eth_ret > -self.exit_thresh_abs or eth_ret > (prev_eth + 1.2) or sol_ret > 0.3 or rsi < 22.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_beta_flatten_or_sol_divergence",
                            "eth_ret": round(eth_ret, 3),
                            "sol_ret": round(sol_ret, 3),
                            "rsi": round(rsi, 2),
                            "close": round(current_close, 3),
                        },
                    )
            return None

        # Check cooldown before opening new positions
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long Entry: Strong ETH positive move, confirmed by SOL return and SOL technicals
        if eth_ret >= self.eth_threshold_long and sol_ret >= self.sol_confirm_thresh:
            if eth_ret >= (prev_eth - 0.3) and current_close >= ema20 and 42.0 <= rsi <= 72.0:
                confidence = min(0.95, 0.60 + (eth_ret - self.eth_threshold_long) * 0.05)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_eth_beta_long_confirmed",
                        "eth_ret": round(eth_ret, 3),
                        "sol_ret": round(sol_ret, 3),
                        "ema20": round(ema20, 3),
                        "rsi": round(rsi, 2),
                        "price": round(current_close, 3),
                    },
                )

        # Short Entry: Strong ETH negative move, confirmed by SOL return and SOL technicals
        if eth_ret <= self.eth_threshold_short and sol_ret <= -self.sol_confirm_thresh:
            if eth_ret <= (prev_eth + 0.3) and current_close <= ema20 and 28.0 <= rsi <= 58.0:
                confidence = min(0.95, 0.60 + (abs(eth_ret) - abs(self.eth_threshold_short)) * 0.05)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_eth_beta_short_confirmed",
                        "eth_ret": round(eth_ret, 3),
                        "sol_ret": round(sol_ret, 3),
                        "ema20": round(ema20, 3),
                        "rsi": round(rsi, 2),
                        "price": round(current_close, 3),
                    },
                )

        return None