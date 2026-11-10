from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeSpreadReversion(Strategy):
    METADATA = {
        "name": "EthBtcRelativeSpreadReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.4
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos = ctx.position
            if pos:
                direction = pos.get("direction")
                entry_price = pos.get("entry_price", ctx.bar.close)
                current_price = ctx.bar.close
                pnl_bps = ((current_price - entry_price) / entry_price * 10000.0) if direction == "long" else ((entry_price - current_price) / entry_price * 10000.0)
                
                # Dynamic reversion exit
                btc_ret = ctx.features.get("btc_return_pct", 0.0)
                eth_ret = ctx.features.get("eth_return_pct", 0.0)
                spread = eth_ret - btc_ret
                
                if direction == "long" and spread >= -0.2:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_spread_reverted", "spread": spread, "pnl_bps": pnl_bps})
                elif direction == "short" and spread <= 0.2:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_spread_reverted", "spread": spread, "pnl_bps": pnl_bps})
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check market regime to reject extreme crisis / meltdown environments
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Negative when ETH lags, positive when ETH outruns

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Long Catch-up: ETH materially lags BTC (spread <= -2.4%) & RSI not overbought (< 58)
        if spread <= -self.spread_threshold and rsi < 58.0:
            confidence = min(0.9, 0.65 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lag_catchup_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "regime": market_regime,
                },
            )

        # Short Spread Reversion: ETH materially outruns BTC (spread >= 2.4%) & RSI not oversold (> 42)
        if spread >= self.spread_threshold and rsi > 42.0:
            confidence = min(0.9, 0.65 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrun_reversion_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "regime": market_regime,
                },
            )

        return None