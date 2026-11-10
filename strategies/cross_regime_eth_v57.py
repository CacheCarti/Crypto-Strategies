from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,  # 4 hours
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.2  # 2.2% divergence threshold
        self.cooldown_bars = 6       # Wait 6 bars (6 hours) after exit
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 12      # Timed structural exit after 12 bars (12h)

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
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25:
            return None

        # Cross-asset returns
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive when ETH outrunning BTC, negative when ETH lagging

        # Structural time-based exit for open positions
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            # Timed decay exit: if thesis hasn't played out within max_hold_bars, exit flat
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "timed_decay_exit", "bars_held": bars_in_trade, "spread": spread}
                )
            
            # Dynamic early profit lock when spread converges back to near zero
            direction = ctx.position_direction()
            if direction == "long" and spread >= -0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reversion_converged_long", "spread": spread}
                )
            elif direction == "short" and spread <= 0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reversion_converged_short", "spread": spread}
                )
            return None

        # Enforce cooldown period after previous trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entering during absolute crisis / liquidation meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)
        if rsi is None or atr is None:
            return None

        # Volatility-based adaptive stops (volatility stop structure)
        price = ctx.bar.close
        atr_pct = (atr / price) * 10000.0  # in bps
        stop_loss_bps = max(180.0, min(360.0, atr_pct * 1.5))
        take_profit_bps = max(240.0, min(500.0, atr_pct * 2.2))

        # Setup 1: ETH severely lags BTC (spread < -threshold) while RSI not already overbought
        if spread <= -self.spread_threshold and rsi < 58.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_btc_lag_catchup_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "atr_bps": atr_pct
                }
            )

        # Setup 2: ETH severely outruns BTC (spread > threshold) while RSI not already oversold
        if spread >= self.spread_threshold and rsi > 42.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_btc_lead_reversion_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "atr_bps": atr_pct
                }
            )

        return None