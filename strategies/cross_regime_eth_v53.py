from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,  # 4 hours
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.2      # Spread divergence threshold in %
        self.cooldown_period = 8         # Bars to wait after an exit
        self.last_exit_bar = -999
        self.max_hold_bars = 16          # Max bars to hold if no exit trigger
        self.entry_bar = 0

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
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Check regime to avoid crisis / extreme volatility chop
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Cross-asset spread calculation: eth_return_pct - btc_return_pct
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Indicators
        rsi = self._rsi(closes, period=14)
        ema20 = self._ema(closes, period=20)
        if rsi is None or ema20 is None:
            return None

        current_price = ctx.bar.close

        # Position management: time-based exit or convergence exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Take profit / mean-reversion achieved
            if pos_dir == "long" and (spread >= 0.0 or rsi > 62.0):
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "long_spread_converged_or_rsi_neutral",
                    "spread": spread,
                    "rsi": rsi,
                    "bars_held": bars_held
                })
            elif pos_dir == "short" and (spread <= 0.0 or rsi < 38.0):
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "short_spread_converged_or_rsi_neutral",
                    "spread": spread,
                    "rsi": rsi,
                    "bars_held": bars_held
                })

            # Horizon fallback
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_hold_reached",
                    "bars_held": bars_held,
                    "spread": spread
                })

            return None

        # Check cooldown to avoid rapid re-entries and overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Entry logic: ETH laggard catch-up (Long)
        # ETH is materially underperforming BTC (spread < -threshold) AND not already in an overbought blow-off
        if spread < -self.spread_threshold and rsi < 48.0 and current_price < ema20 * 1.01:
            confidence = min(0.85, 0.55 + abs(spread) / 10.0)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_lagging_btc_mean_reversion",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": current_price
                }
            )

        # Entry logic: ETH outrunning BTC (Short)
        # ETH is significantly outperforming BTC (spread > threshold) AND not already oversold
        if spread > self.spread_threshold and rsi > 52.0 and current_price > ema20 * 0.99:
            confidence = min(0.85, 0.55 + abs(spread) / 10.0)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": current_price
                }
            )

        return None