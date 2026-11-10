from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0      # 2% spread threshold
        self.rsi_period = 14
        self.ema_period = 24
        self.cooldown_bars = 6          # Mandatory wait after exit to ensure trade independence
        self.max_hold_bars = 16         # Max bars in position before timed mean-reversion exit
        self.last_exit_bar = -999
        self.entry_bar = 0

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Fetch returns for relative strength computation
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        if rsi is None or ema is None:
            return None

        current_close = ctx.bar.close
        pos_dir = ctx.position_direction()

        # Manage open position: dynamic mean-reversion exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Reversion of spread back towards neutral or overextended RSI
            if pos_dir == "long":
                if spread <= 0.0 or rsi > 70.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_mean_reversion_or_exhaustion",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held
                    })
            elif pos_dir == "short":
                if spread >= 0.0 or rsi < 30.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_mean_reversion_or_exhaustion",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held
                    })
            return None

        # Enforce mandatory post-exit cooldown to avoid churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entry during extreme market stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Entry: ETH strongly outperforming BTC, price above short-term trend, not overbought
        if spread >= self.spread_threshold and current_close > ema and 45.0 <= rsi <= 68.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_breakout",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2)
                }
            )

        # Short Entry: ETH strongly underperforming BTC, price below short-term trend, not oversold
        if spread <= -self.spread_threshold and current_close < ema and 32.0 <= rsi <= 55.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_breakdown",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2)
                }
            )

        return None