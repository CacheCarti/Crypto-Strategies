from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold for swing
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        # Structural time-based exit guard
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_exit_max_hold", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset returns (24h return percentage)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive means ETH outrunning BTC, negative means ETH lagging

        highs = ctx.highs(25)
        lows = ctx.lows(25)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or ctx.bar.close == 0:
            return None

        # Volatility-scaled dynamic stops
        atr_bps = (atr / ctx.bar.close) * 10000.0
        sl_bps = max(180.0, min(380.0, atr_bps * 1.6))
        tp_bps = max(280.0, min(550.0, atr_bps * 2.5))

        rsi = self._rsi(closes, period=14)
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Selective Spread Triggers (requires material spread >= 2.5% divergence + local RSI confirmation)
        # 1. ETH lagging BTC materially (spread <= -2.5%) -> Catch-up Long
        if spread <= -2.5 and rsi < 55.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(spread) * 0.04)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lag_catchup_long",
                    "spread_pct": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        # 2. ETH outrunning BTC materially (spread >= 2.5%) -> Mean-Reversion Short
        if spread >= 2.5 and rsi > 45.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(spread) * 0.04)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrun_reversion_short",
                    "spread_pct": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index