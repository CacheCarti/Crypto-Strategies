from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.2  # 2.2% return differential threshold
        self.rsi_period = 14
        self.ema_period = 30

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

    def _ema(self, values: list, period: int) -> Optional[float]:
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
        closes = ctx.closes(45)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard: must wait several bars after any position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset spread: positive spread means ETH is outperforming BTC
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Technical context on ETH price
        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        if rsi is None or ema is None:
            return None

        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")

        # Avoid entering during disorderly meltdown conditions
        if regime in ("MELTDOWN", "CRISIS"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": regime}
                )
            return None

        # Manage existing position exits based on spread normalization or oscillator exhaustion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # ETH has caught up or RSI reached overbought
                if spread >= 0.0 or rsi >= 68.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_target_reached",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )
            elif pos_dir == "short":
                # ETH premium faded or RSI hit oversold
                if spread <= 0.0 or rsi <= 32.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_target_reached",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )
            return None

        # Setup 1: ETH severely lagging BTC (spread < -threshold) while ETH RSI not overbought
        # Prior evidence note: avoid longing purely in strong structural downtrend (keep price near/above EMA or RSI turning from oversold)
        if spread <= -self.spread_threshold and rsi < 55.0 and rsi > 28.0:
            confidence = min(0.9, 0.55 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price
                }
            )

        # Setup 2: ETH outrunning BTC (spread > threshold) while ETH RSI elevated
        if spread >= self.spread_threshold and rsi > 45.0 and rsi < 72.0:
            confidence = min(0.9, 0.55 + spread * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price
                }
            )

        return None