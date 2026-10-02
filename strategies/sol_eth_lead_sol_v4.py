from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthBetaAmplifier(Strategy):
    METADATA = {
        "name": "SolEthBetaAmplifier",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 12
        self.slow_ema_period = 26
        self.rsi_period = 14
        self.eth_threshold = 2.2
        self.cooldown_bars = 4
        self.last_exit_bar = -999
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
        closes = ctx.closes(self.slow_ema_period + 5)
        if len(closes) < self.slow_ema_period + 1:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)

        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            self.prev_eth_ret = eth_ret
            return None

        current_price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic when in position
        if has_pos:
            if pos_dir == "long":
                if eth_ret < 0.5 or current_price < ema_fast or rsi > 78.0:
                    self.prev_eth_ret = eth_ret
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "eth_momentum_flattened_or_sol_exhaustion",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "rsi": rsi,
                            "price": current_price,
                        },
                    )
            elif pos_dir == "short":
                if eth_ret > -0.5 or current_price > ema_fast or rsi < 22.0:
                    self.prev_eth_ret = eth_ret
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "eth_rebound_or_sol_oversold_bounce",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "rsi": rsi,
                            "price": current_price,
                        },
                    )
            self.prev_eth_ret = eth_ret
            return None

        # Mandatory Cooldown after prior trade
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_eth_ret = eth_ret
            return None

        # Long Entry: Strong and accelerating ETH returns confirmed by SOL beta and trend
        eth_rising = eth_ret > self.prev_eth_ret
        if eth_ret >= self.eth_threshold and eth_rising and sol_ret > 0.5:
            if current_price > ema_fast and ema_fast > ema_slow and 42.0 <= rsi <= 68.0:
                self.prev_eth_ret = eth_ret
                conf = min(0.9, 0.6 + abs(eth_ret) * 0.03)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_bull_driver_sol_beta_follow",
                        "eth_return_pct": eth_ret,
                        "sol_return_pct": sol_ret,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price,
                    },
                )

        # Short Entry: Declining ETH returns confirmed by SOL negative beta and downtrend
        eth_falling = eth_ret < self.prev_eth_ret
        if eth_ret <= -self.eth_threshold and eth_falling and sol_ret < -0.5:
            if current_price < ema_fast and ema_fast < ema_slow and 32.0 <= rsi <= 58.0:
                self.prev_eth_ret = eth_ret
                conf = min(0.9, 0.6 + abs(eth_ret) * 0.03)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_bear_driver_sol_beta_follow",
                        "eth_return_pct": eth_ret,
                        "sol_return_pct": sol_ret,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price,
                    },
                )

        self.prev_eth_ret = eth_ret
        return None