from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeSpreadReversion(Strategy):
    METADATA = {
        "name": "EthBtcRelativeSpreadReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Structural time-stop exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "timed_structural_exit", "bars_held": bars_held}
                )
            return None

        # Enforce entry cooldown after last exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, 14)
        if rsi is None or atr is None:
            return None

        current_price = ctx.bar.close
        # Volatility-scaled dynamic stop & target
        atr_bps = (atr / current_price) * 10000.0
        sl_bps = max(200.0, min(500.0, atr_bps * 1.8))
        tp_bps = max(300.0, min(750.0, atr_bps * 2.8))

        # Spread Long: ETH significantly lagging BTC (eth - btc < -2.0%) with oversold/neutral ETH RSI
        if spread <= -self.spread_threshold and rsi < 55.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(spread) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "atr_bps": atr_bps
                }
            )

        # Spread Short: ETH significantly leading BTC (eth - btc > 2.0%) with overbought/neutral ETH RSI
        if spread >= self.spread_threshold and rsi > 45.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(spread) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_mean_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "atr_bps": atr_bps
                }
            )

        return None