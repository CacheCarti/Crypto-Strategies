from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcCrossAssetCatchup(Strategy):
    METADATA = {
        "name": "ETH-BTC Cross Asset Catchup",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_period = 20
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.spread_threshold = 2.2

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

    def _ema(self, values, period):
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

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi_val = self._rsi(closes, self.rsi_period)
        ema_val = self._ema(closes, self.ema_period)
        if rsi_val is None or ema_val is None:
            return None

        current_price = ctx.bar.close

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if spread >= 0.8 or rsi_val >= 68.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_spread_reverted_or_rsi_overbought",
                            "spread": spread,
                            "btc_ret": btc_ret,
                            "eth_ret": eth_ret,
                            "rsi": rsi_val,
                            "price": current_price
                        }
                    )
            elif pos_dir == "short":
                if spread <= -0.8 or rsi_val <= 32.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_spread_reverted_or_rsi_oversold",
                            "spread": spread,
                            "btc_ret": btc_ret,
                            "eth_ret": eth_ret,
                            "rsi": rsi_val,
                            "price": current_price
                        }
                    )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if spread < -self.spread_threshold and btc_ret > -3.0 and rsi_val < 52.0:
            conf = min(0.85, 0.55 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_entry",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi_val,
                    "ema20": ema_val,
                    "price": current_price
                }
            )

        if spread > self.spread_threshold and rsi_val > 48.0:
            conf = min(0.85, 0.55 + spread / 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_vs_btc_mean_reversion_entry",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi_val,
                    "ema20": ema_val,
                    "price": current_price
                }
            )

        return None