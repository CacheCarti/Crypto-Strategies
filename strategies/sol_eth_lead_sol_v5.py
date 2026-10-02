from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthBetaFollower(Strategy):
    METADATA = {
        "name": "SolEthBetaFollower",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 25
        self.rsi_period = 14
        self.eth_threshold = 2.0
        self.sol_threshold = 0.8
        self.eth_exit_threshold = 0.5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.prev_eth_return = 0.0

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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)
        eth_rising = eth_ret > self.prev_eth_return
        eth_falling = eth_ret < self.prev_eth_return
        self.prev_eth_return = eth_ret

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic for open positions
        if has_pos:
            if pos_dir == "long":
                if eth_ret < self.eth_exit_threshold or current_price < fast_ema or rsi > 78.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "eth_momentum_flattened_or_trend_reversal",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "rsi": rsi,
                            "price": current_price,
                            "fast_ema": fast_ema
                        }
                    )
            elif pos_dir == "short":
                if eth_ret > -self.eth_exit_threshold or current_price > fast_ema or rsi < 22.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "eth_momentum_recovered_or_trend_reversal",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "rsi": rsi,
                            "price": current_price,
                            "fast_ema": fast_ema
                        }
                    )
            return None

        # Mandatory Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long Entry: Strong positive ETH beta, confirmed by SOL return & price momentum
        if (
            eth_ret >= self.eth_threshold
            and eth_rising
            and sol_ret >= self.sol_threshold
            and current_price > fast_ema
            and fast_ema > slow_ema
            and 48.0 < rsi < 72.0
        ):
            confidence = min(0.9, 0.60 + (eth_ret / 10.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "confirmed_bullish_eth_beta_breakout",
                    "eth_return_pct": eth_ret,
                    "sol_return_pct": sol_ret,
                    "rsi": rsi,
                    "price": current_price,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema
                }
            )

        # Short Entry: Strong negative ETH beta, confirmed by SOL return & price breakdown
        if (
            eth_ret <= -self.eth_threshold
            and eth_falling
            and sol_ret <= -self.sol_threshold
            and current_price < fast_ema
            and fast_ema < slow_ema
            and 28.0 < rsi < 52.0
        ):
            confidence = min(0.9, 0.60 + (abs(eth_ret) / 10.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "confirmed_bearish_eth_beta_breakdown",
                    "eth_return_pct": eth_ret,
                    "sol_return_pct": sol_ret,
                    "rsi": rsi,
                    "price": current_price,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema
                }
            )

        return None