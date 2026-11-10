from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,  # 4 hours intended swing hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -999
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        # Structural time-based exit guard
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "timed_exit_max_hold_reached",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard after position closure to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime & Crisis safety filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive when ETH outperforms BTC

        rsi = self._rsi(closes, period=14)
        atr = self._atr(highs, lows, closes, period=14)
        if rsi is None or atr is None:
            return None

        # Volatility-adjusted stop-loss & take-profit calculation
        atr_bps = (atr / ctx.bar.close) * 10000.0
        dynamic_sl = max(180.0, min(380.0, atr_bps * 1.6))
        dynamic_tp = max(260.0, min(500.0, dynamic_sl * 1.5))

        # Setup 1: ETH severely lags BTC while not locally overbought -> catch-up long
        if spread <= -2.2 and rsi < 48.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.90, 0.60 + min(abs(spread) / 10.0, 0.25))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "cross_regime_eth_lag_catchup",
                    "spread_pct": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "atr_bps": atr_bps
                }
            )

        # Setup 2: ETH severely outpaces BTC while not locally oversold -> spread reversion short
        if spread >= 2.2 and rsi > 52.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.90, 0.60 + min(abs(spread) / 10.0, 0.25))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "cross_regime_eth_outrun_reversion",
                    "spread_pct": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "atr_bps": atr_bps
                }
            )

        return None