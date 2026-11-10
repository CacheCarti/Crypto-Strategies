from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthTrend(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.spread_threshold = 2.0  # 2% spread threshold
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26

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
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        # Feature spread computation: ETH 24h return vs BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Technical confirmation indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Mean-reversion exit or momentum exhaustion
            if pos_dir == "long":
                if spread < 0.2 or rsi > 78.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_rsi_overbought",
                            "spread": round(spread, 3),
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            elif pos_dir == "short":
                if spread > -0.2 or rsi < 22.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_rsi_oversold",
                            "spread": round(spread, 3),
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entry during extreme market stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Selective entries: Spread threshold + trend alignment + RSI not overextended
        # Long entry: ETH outperforming BTC by >2%, price above EMA trend, RSI healthy
        if spread >= self.spread_threshold and current_price > ema_fast and ema_fast > ema_slow and 45.0 < rsi < 70.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperforming_btc_trend_confirmed",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "price": round(current_price, 2),
                },
            )

        # Short entry: ETH underperforming BTC by >2%, price below EMA trend, RSI healthy
        if spread <= -self.spread_threshold and current_price < ema_fast and ema_fast < ema_slow and 30.0 < rsi < 55.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperforming_btc_trend_confirmed",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "price": round(current_price, 2),
                },
            )

        return None