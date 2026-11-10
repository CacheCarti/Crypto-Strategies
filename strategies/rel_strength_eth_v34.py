from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.reversion_threshold = 0.5
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.prev_spread = 0.0

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _vol_ratio(self, ctx: BarContext, period: int = 24) -> float:
        vols = ctx.volumes(period + 1)
        if len(vols) < period + 1:
            return 1.0
        avg_vol = sum(vols[:-1]) / period
        return (vols[-1] / avg_vol) if avg_vol > 0 else 1.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Volatility regime gate: avoid entries during deep crisis / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        # Position exit management on spread mean-reversion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and spread <= self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif pos_dir == "short" and spread >= -self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_spread = spread
            return None

        curr_close = closes[-1]
        vol_rat = self._vol_ratio(ctx, 20)

        # Trigger on spread crossover transitions to prevent continuous firing
        long_cross = (self.prev_spread <= self.spread_threshold and spread > self.spread_threshold)
        short_cross = (self.prev_spread >= -self.spread_threshold and spread < -self.spread_threshold)

        self.prev_spread = spread

        # Relative strength continuation long with EMA trend and volume state confirmation
        if long_cross and curr_close > ema20 and vol_rat >= 0.8:
            conf = min(0.9, 0.6 + 0.05 * min(spread - self.spread_threshold, 4.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_strength_breakout_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "vol_ratio": vol_rat
                }
            )

        # Relative weakness short with EMA resistance confirmation
        if short_cross and curr_close < ema20 and vol_rat >= 0.8:
            conf = min(0.9, 0.6 + 0.05 * min(abs(spread) - self.spread_threshold, 4.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_weakness_breakdown_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "vol_ratio": vol_rat
                }
            )

        return None