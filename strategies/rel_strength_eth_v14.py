from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthBreakout(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0  # 2% spread threshold
        self.rsi_period = 14
        self.ema_fast = 12
        self.ema_slow = 26

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
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Regime & Crisis Filter (avoid entering during extreme dislocated panics)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "crisis_score": crisis_score, "regime": market_regime}
                )
            return None

        # Cross-asset spread calculation
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Confirmation indicators
        ema_fast_val = self._ema(closes, self.ema_fast)
        ema_slow_val = self._ema(closes, self.ema_slow)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast_val is None or ema_slow_val is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Position management / mean-reversion exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion of spread or momentum exhaustion
            if pos_dir == "long":
                if spread < 0.2 or rsi > 78.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_overbought",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )
            elif pos_dir == "short":
                if spread > -0.2 or rsi < 22.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_oversold",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )
            return None

        # Enforce entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: Rel-strength breakout confirmed by trend & non-exhausted RSI
        # Long: ETH leading BTC by > 2.0%, local trend intact (price > EMA fast > EMA slow), RSI not overbought
        if (
            spread >= self.spread_threshold
            and current_price > ema_fast_val > ema_slow_val
            and 48.0 < rsi < 68.0
        ):
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_trend_breakout",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast_val,
                    "ema_slow": ema_slow_val,
                    "price": current_price,
                }
            )

        # Short: ETH lagging BTC by > 2.0%, local trend down (price < EMA fast < EMA slow), RSI not oversold
        if (
            spread <= -self.spread_threshold
            and current_price < ema_fast_val < ema_slow_val
            and 32.0 < rsi < 52.0
        ):
            confidence = min(0.9, 0.55 + (-spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_trend_breakdown",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast_val,
                    "ema_slow": ema_slow_val,
                    "price": current_price,
                }
            )

        return None