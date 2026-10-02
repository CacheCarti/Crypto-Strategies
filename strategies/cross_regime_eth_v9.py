from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadReversion(Strategy):
    METADATA = {
        "name": "ETH BTC Spread Catchup & Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.spread_threshold = 2.4
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.last_entry_bar = -100

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
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature readings
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret  # Positive when BTC leads / ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        ema_trend = self._ema(closes, 24)
        if rsi is None or ema_trend is None:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion completed or spread normalized
            if pos_dir == "long" and (spread <= 0.2 or rsi >= 68.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_spread_reverted_or_rsi_overbought",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                    },
                )
            elif pos_dir == "short" and (spread >= -0.2 or rsi <= 32.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_spread_reverted_or_rsi_oversold",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                    },
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Long Setup: BTC leading strongly, ETH lagging, ETH not overbought
        if spread >= self.spread_threshold and btc_ret > -1.0 and rsi < 58.0:
            confidence = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.06)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        # Short Setup: ETH outrunning BTC by large margin, ETH overextended (RSI > 52)
        if spread <= -self.spread_threshold and eth_ret > 1.0 and rsi > 52.0:
            confidence = min(0.85, 0.55 + (-spread - self.spread_threshold) * 0.06)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_vs_btc_mean_reversion",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        return None