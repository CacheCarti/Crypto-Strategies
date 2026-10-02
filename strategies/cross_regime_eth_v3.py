from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeSpreadReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Spread Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.4
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_pos = 0

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
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        ema_fast = self._ema(closes, 9)
        ema_slow = self._ema(closes, 21)
        if ema_fast is None or ema_slow is None:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()

            # Flat signal when cross-asset divergence converges
            if direction == "long" and spread <= 0.2 and self.bars_in_pos >= 2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_lag_spread_converged",
                        "spread": round(spread, 3),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                        "rsi": round(rsi, 2),
                        "bars_held": self.bars_in_pos,
                    },
                )
            elif direction == "short" and spread >= -0.2 and self.bars_in_pos >= 2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_lead_spread_converged",
                        "spread": round(spread, 3),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                        "rsi": round(rsi, 2),
                        "bars_held": self.bars_in_pos,
                    },
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter (avoid opening into crisis/meltdown)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # LONG SETUP: BTC leading ETH significantly; ETH is expected to catch up.
        # Filter: RSI < 62 to prevent buying severely overbought tops.
        if spread >= self.spread_threshold and rsi < 62.0:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "cross_asset_eth_lag_catchup",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                },
            )

        # SHORT SETUP: ETH overextended relative to BTC; expect mean reversion.
        # Filter: RSI > 38 to prevent shorting deeply oversold bottoms.
        if spread <= -self.spread_threshold and rsi > 38.0:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "cross_asset_eth_overextended_reversion",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                },
            )

        return None