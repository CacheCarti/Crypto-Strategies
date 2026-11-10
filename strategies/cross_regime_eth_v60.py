from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_history = []
        self.spread_threshold = 1.8
        self.rsi_period = 14

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > 60:
            self.spread_history.pop(0)

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            pos = ctx.position
            entry_price = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close

            if pos_dir == "long":
                ret_bps = ((ctx.bar.close - entry_price) / entry_price) * 10000.0
                if spread >= 0.0 or ret_bps >= self.METADATA["declared_tp_bps"] or ret_bps <= -self.METADATA["declared_sl_bps"]:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "long_spread_reverted_or_target", "spread": spread, "ret_bps": ret_bps}
                    )
            elif pos_dir == "short":
                ret_bps = ((entry_price - ctx.bar.close) / entry_price) * 10000.0
                if spread <= 0.0 or ret_bps >= self.METADATA["declared_tp_bps"] or ret_bps <= -self.METADATA["declared_sl_bps"]:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "short_spread_reverted_or_target", "spread": spread, "ret_bps": ret_bps}
                    )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        if len(self.spread_history) < 20:
            return None

        recent_spreads = self.spread_history[-20:]
        mean_spread = sum(recent_spreads) / len(recent_spreads)
        variance = sum((x - mean_spread) ** 2 for x in recent_spreads) / len(recent_spreads)
        std_spread = math.sqrt(variance) if variance > 0.0 else 0.001

        z_spread = (spread - mean_spread) / std_spread if std_spread > 0.0 else 0.0

        prev_spread = self.spread_history[-2] if len(self.spread_history) >= 2 else spread

        if z_spread <= -self.spread_threshold and prev_spread < spread and rsi < 50.0:
            conf = min(0.85, 0.5 + abs(z_spread) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_inflection",
                    "z_spread": round(z_spread, 2),
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2)
                }
            )

        if z_spread >= self.spread_threshold and prev_spread > spread and rsi > 50.0:
            conf = min(0.85, 0.5 + abs(z_spread) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion",
                    "z_spread": round(z_spread, 2),
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2)
                }
            )

        return None