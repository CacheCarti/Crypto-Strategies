from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthTrend(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Trend",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0        # ETH 24h return out/underperforming BTC by 2.0%
        self.cooldown_period = 8           # Bars to wait after exit before entering again
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16            # Max 16 hours hold per trade to prevent staleness

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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Fetch platform relative returns
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Price indicators for confirmation
        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 26)
        rsi = self._rsi(closes, 14)
        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        current_close = ctx.bar.close

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Mean-reversion exit condition on spread convergence
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_long_exit", "spread": spread, "bars_held": bars_held}
                )
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_short_exit", "spread": spread, "bars_held": bars_held}
                )

            # Time-based stop for stagnation
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_max_hold", "spread": spread, "bars_held": bars_held}
                )

            return None

        # Cooldown guard after closing previous position
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Market regime filter: avoid choppy crisis regime
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup: ETH leads BTC (+2.0%), confirmed by trend & non-overbought RSI
        if spread >= self.spread_threshold and current_close > ema_fast > ema_slow and rsi < 68.0:
            confidence = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.05)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperforming_btc_trend_aligned",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow
                }
            )

        # Short Setup: ETH lags BTC (-2.0%), confirmed by trend & non-oversold RSI
        if spread <= -self.spread_threshold and current_close < ema_fast < ema_slow and rsi > 32.0:
            confidence = min(0.85, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperforming_btc_trend_aligned",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow
                }
            )

        return None