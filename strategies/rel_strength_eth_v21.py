from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.spread_threshold = 2.0  # 2.0% return spread threshold
        self.max_hold_bars = 16

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        # Features & Indicators
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, 14)
        ema_fast = self._ema(closes, 9)
        ema_slow = self._ema(closes, 21)
        
        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close

        # Position Management & Mean-Reversion Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", current_price) if ctx.position else current_price
            
            # Check mean-reversion exit (spread normalization) or exhaustion
            if direction == "long":
                if spread <= 0.2 or rsi > 70.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "spread_reverted_or_rsi_overbought",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )
            elif direction == "short":
                if spread >= -0.2 or rsi < 30.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "spread_reverted_or_rsi_oversold",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid market meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Signal Entry Logic: Relative Strength combined with trend confirmation
        # 1. Long Entry: ETH outperforming BTC by > 2.0%, local trend intact (price > EMA21), RSI not exhausted (< 65)
        if spread >= self.spread_threshold and current_price > ema_slow and rsi < 65.0:
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_relative_outperformance_trend_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_slow": ema_slow,
                    "price": current_price
                }
            )

        # 2. Short Entry: ETH lagging BTC by > 2.0%, local trend weak (price < EMA21), RSI not oversold (> 35)
        if spread <= -self.spread_threshold and current_price < ema_slow and rsi > 35.0:
            conf = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_relative_underperformance_trend_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_slow": ema_slow,
                    "price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index