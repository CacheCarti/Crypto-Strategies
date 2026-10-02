from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class EthBtcRelativeStrengthRotation(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Momentum Rotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 1.2
        self.ema_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        ema_val = self._ema(closes, self.ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_val is None or rsi_val is None:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit management: mean reversion of spread or dynamic trend loss
        if has_pos:
            if pos_dir == "long":
                if spread <= 0.0 or current_close < (ema_val * 0.992):
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_trend_loss",
                            "spread": spread,
                            "eth_ret": eth_ret,
                            "btc_ret": btc_ret,
                            "rsi": rsi_val,
                            "ema": ema_val,
                            "close": current_close,
                        },
                    )
            elif pos_dir == "short":
                if spread >= 0.0 or current_close > (ema_val * 1.008):
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_trend_loss",
                            "spread": spread,
                            "eth_ret": eth_ret,
                            "btc_ret": btc_ret,
                            "rsi": rsi_val,
                            "ema": ema_val,
                            "close": current_close,
                        },
                    )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown conditions
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Entry: ETH outperforming BTC with price confirmed above EMA
        if spread >= self.spread_threshold and current_close >= ema_val and rsi_val < 80.0:
            excess = spread - self.spread_threshold
            confidence = min(0.9, 0.55 + excess * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_outperformance_momentum_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi_val,
                    "ema": ema_val,
                    "close": current_close,
                },
            )

        # Short Entry: ETH underperforming BTC with price confirmed below EMA
        if spread <= -self.spread_threshold and current_close <= ema_val and rsi_val > 20.0:
            excess = abs(spread) - self.spread_threshold
            confidence = min(0.9, 0.55 + excess * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_underperformance_momentum_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi_val,
                    "ema": ema_val,
                    "close": current_close,
                },
            )

        return None