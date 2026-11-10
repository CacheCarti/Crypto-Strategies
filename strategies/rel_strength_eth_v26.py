from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # 6 hours average swing
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0  # 2.0% return spread threshold
        self.ema_fast = 12
        self.ema_slow = 26
        self.rsi_period = 14

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(45)
        if len(closes) < 40:
            return None

        # Feature spread: ETH 24h return minus BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Price confirmation indicators
        ema_f = self._ema(closes, self.ema_fast)
        ema_s = self._ema(closes, self.ema_slow)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_f is None or ema_s is None or rsi is None:
            return None

        # Check exits on mean-reversion of the spread if holding position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion of spread back towards 0
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reversion_exit_long", "spread": spread, "rsi": rsi}
                )
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reversion_exit_short", "spread": spread, "rsi": rsi}
                )
            return None

        # Enforce post-exit cooldown to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Entry: ETH outperforming BTC cleanly + technical trend confirmation
        # Prior evidence note: avoid overbought exhaustion entries by gating RSI
        if spread >= self.spread_threshold and ema_f > ema_s and 45.0 < rsi < 68.0:
            confidence = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_leads_btc_momentum",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_diff": ema_f - ema_s,
                }
            )

        # Short Entry: ETH severely lagging BTC + technical weakness
        if spread <= -self.spread_threshold and ema_f < ema_s and 32.0 < rsi < 55.0:
            confidence = min(0.85, 0.55 + (-spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_lags_btc_continuation",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_diff": ema_f - ema_s,
                }
            )

        return None