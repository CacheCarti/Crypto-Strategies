from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,  # ~4 hours expected horizon
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 16
        self.entry_bar = -999

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
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))
        avg_g = sum(gains[-period:]) / period
        avg_l = sum(losses[-period:]) / period
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        # Structural time-based exit to prevent allocation giveback chop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_exit_max_hold", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset returns
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive means ETH outrunning BTC

        # Local technical confirmations
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)
        if rsi is None or atr is None:
            return None

        # Volatility-adjusted stop & profit targets based on ATR
        atr_pct = (atr / ctx.bar.close) * 10000.0
        dynamic_sl = max(180.0, min(380.0, atr_pct * 1.5))
        dynamic_tp = max(280.0, min(550.0, dynamic_sl * 1.5))

        # Filter out extreme crisis regimes to preserve capital
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.70:
            return None

        # Long Catch-Up Setup: ETH significantly lagging BTC, local oversold bounce
        # btc_ret > -1.0 ensures BTC isn't in freefall collapse
        if spread <= -2.5 and btc_ret > -1.0 and rsi < 42.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "dynamic_sl": round(dynamic_sl, 1)
                }
            )

        # Short Mean-Reversion Setup: ETH significantly outrunning BTC, local exhaustion
        if spread >= 2.8 and rsi > 58.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.70,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "dynamic_sl": round(dynamic_sl, 1)
                }
            )

        return None