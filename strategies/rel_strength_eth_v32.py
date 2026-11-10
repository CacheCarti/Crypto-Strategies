from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Momentum Rotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.2
        self.ema_filter_period = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_entry_bar = -999

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Market regime gating: avoid crisis/meltdown to address holdout breakdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        # Cross-asset relative spread
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Fast trend filter on ETH close to confirm local momentum direction
        ema24 = self._ema(closes, self.ema_filter_period)
        if ema24 is None:
            return None

        current_price = ctx.bar.close

        # Position exit logic: mean reversion of spread or momentum fading
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_mean_reverted_long", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret
                })
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_mean_reverted_short", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret
                })
            return None

        # Cooldown guard: prevent rapid whipsawing after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Sentiment regime gating: do not buy into extreme panic or short into euphoria
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Long Entry: ETH substantially outperforming BTC + ETH price above trend + not extreme greed
        if spread >= self.spread_threshold and current_price > ema24 and fg_index <= 80:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperforming_btc_rotation",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema24": ema24,
                    "fg_index": fg_index
                }
            )

        # Short Entry: ETH substantially underperforming BTC + ETH price below trend + not extreme fear
        if spread <= -self.spread_threshold and current_price < ema24 and fg_index >= 20:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperforming_btc_rotation",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema24": ema24,
                    "fg_index": fg_index
                }
            )

        return None