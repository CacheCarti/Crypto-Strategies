from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,  # 4-hour target hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0  # 2.0% divergence threshold
        self.spread_history = []

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
        if ctx.bar_index < self.warmup:
            return None

        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < self.warmup:
            return None

        # Cross-asset spread calculation: ETH 24h return minus BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive when ETH outperforms, negative when ETH lags

        self.spread_history.append(spread)
        if len(self.spread_history) > 50:
            self.spread_history.pop(0)

        # Volatility & Momentum confirmation
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)
        if rsi is None or atr is None:
            return None

        # Volatility state: relative ATR to avoid dead regimes or blowout crises
        norm_atr = (atr / closes[-1]) * 10000.0  # ATR in bps
        vol_filter = 25.0 <= norm_atr <= 250.0

        # Market regime filter from context
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Active position management: mean-reversion exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # If long ETH (caught up to BTC) and spread recovered
            if pos_dir == "long" and spread >= -0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_long", "spread": spread})
            # If short ETH (reverted back to BTC) and spread normalized
            if pos_dir == "short" and spread <= 0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_short", "spread": spread})
            return None

        # Mandatory Cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if not vol_filter:
            return None

        # Long Setup: ETH significantly lags BTC, but ETH momentum is turning (RSI not in free-fall)
        # ETH lagging: spread <= -2.0%, RSI oversold/turning up from local weakness (32 <= RSI <= 48)
        if spread <= -self.spread_threshold and 32.0 <= rsi <= 48.0:
            confidence = min(0.85, 0.5 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "norm_atr": norm_atr,
                },
            )

        # Short Setup: ETH significantly outruns BTC, ETH momentum overheated (55 <= RSI <= 72)
        # ETH outrunning: spread >= +2.0%
        if spread >= self.spread_threshold and 55.0 <= rsi <= 72.0:
            confidence = min(0.85, 0.5 + abs(spread) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_btc_mean_revert",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "norm_atr": norm_atr,
                },
            )

        return None