from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBetaEthFollow(Strategy):
    METADATA = {
        "name": "SolBetaEthFollow",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.prev_eth_ret = 0.0
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.cooldown_bars = 4
        self.eth_entry_thresh = 2.2
        self.sol_confirm_thresh = 0.8
        self.eth_exit_thresh = 0.7
        self.ema_period = 20
        self.rsi_period = 14

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)
        curr_price = ctx.bar.close

        ema_val = self._ema(closes, self.ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_val is None or rsi_val is None:
            self.prev_eth_ret = eth_ret
            return None

        eth_momentum_up = eth_ret >= self.eth_entry_thresh and eth_ret > self.prev_eth_ret
        eth_momentum_down = eth_ret <= -self.eth_entry_thresh and eth_ret < self.prev_eth_ret

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if pos_dir == "long":
                if eth_ret < self.eth_exit_thresh:
                    should_exit = True
                    exit_reason = "eth_driver_flattened_long"
                elif sol_ret < -0.5:
                    should_exit = True
                    exit_reason = "sol_divergence_loss_long"
                elif rsi_val > 80.0:
                    should_exit = True
                    exit_reason = "rsi_overbought_long"
            elif pos_dir == "short":
                if eth_ret > -self.eth_exit_thresh:
                    should_exit = True
                    exit_reason = "eth_driver_flattened_short"
                elif sol_ret > 0.5:
                    should_exit = True
                    exit_reason = "sol_divergence_loss_short"
                elif rsi_val < 20.0:
                    should_exit = True
                    exit_reason = "rsi_oversold_short"

            if should_exit:
                self.prev_eth_ret = eth_ret
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "eth_ret": eth_ret,
                        "sol_ret": sol_ret,
                        "rsi": rsi_val,
                        "price": curr_price,
                    },
                )

            self.prev_eth_ret = eth_ret
            return None

        # Cooldown guard for new entries
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.prev_eth_ret = eth_ret
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            self.prev_eth_ret = eth_ret
            return None

        # Entry logic
        # Long Setup: ETH surging & accelerating + SOL confirmed positive beta + Price above trend + Room in RSI
        if (
            eth_momentum_up
            and sol_ret >= self.sol_confirm_thresh
            and curr_price > ema_val
            and 45.0 <= rsi_val <= 75.0
        ):
            self.last_entry_bar = ctx.bar_index
            self.prev_eth_ret = eth_ret
            conf = min(0.9, 0.6 + (eth_ret - self.eth_entry_thresh) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_eth_beta_follow_long",
                    "eth_ret": eth_ret,
                    "sol_ret": sol_ret,
                    "rsi": rsi_val,
                    "ema20": ema_val,
                    "price": curr_price,
                },
            )

        # Short Setup: ETH dropping & accelerating + SOL confirmed negative beta + Price below trend + Room in RSI
        if (
            eth_momentum_down
            and sol_ret <= -self.sol_confirm_thresh
            and curr_price < ema_val
            and 25.0 <= rsi_val <= 55.0
        ):
            self.last_entry_bar = ctx.bar_index
            self.prev_eth_ret = eth_ret
            conf = min(0.9, 0.6 + (abs(eth_ret) - self.eth_entry_thresh) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_eth_beta_follow_short",
                    "eth_ret": eth_ret,
                    "sol_ret": sol_ret,
                    "rsi": rsi_val,
                    "ema20": ema_val,
                    "price": curr_price,
                },
            )

        self.prev_eth_ret = eth_ret
        return None