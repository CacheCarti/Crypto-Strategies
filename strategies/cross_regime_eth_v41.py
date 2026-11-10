from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.spread_threshold = 1.8  # 1.8% return difference between ETH and BTC
        self.rsi_period = 14

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track open trade holding duration
        if ctx.has_position():
            self.bars_in_trade += 1

        # Retrieve cross-asset 24h return percentages
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive means ETH leads, negative means ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        pos_dir = ctx.position_direction()

        # Exit logic for mean-reversion completion or time decay
        if pos_dir == "long":
            if spread >= 0.2 or rsi >= 60.0 or self.bars_in_trade >= 8:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted_or_time", "spread": spread, "rsi": rsi, "bars_held": self.bars_in_trade}
                )
        elif pos_dir == "short":
            if spread <= -0.2 or rsi <= 40.0 or self.bars_in_trade >= 8:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted_or_time", "spread": spread, "rsi": rsi, "bars_held": self.bars_in_trade}
                )

        # Do not enter if in cooldown or already positioned
        if ctx.has_position():
            return None
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Gate out severe market panic/crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long ETH Catch-up: ETH lags BTC by >= 1.8%, confirmed by non-overbought local RSI
        if spread <= -self.spread_threshold and rsi < 48.0:
            confidence = min(0.85, 0.55 + abs(spread) * 0.05)
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                }
            )

        # Short ETH Exhaustion: ETH outpaces BTC by >= 1.8%, confirmed by non-oversold local RSI
        if spread >= self.spread_threshold and rsi > 52.0:
            confidence = min(0.85, 0.55 + abs(spread) * 0.05)
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_vs_btc",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                }
            )

        return None