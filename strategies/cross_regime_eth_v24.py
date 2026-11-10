from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcCatchupReversion(Strategy):
    METADATA = {
        "name": "EthBtcCatchupReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.rsi_period = 14
        self.spread_threshold = 1.8  # 1.8% return difference between BTC and ETH

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
        closes = ctx.closes(40)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        
        if len(closes) < 40 or len(highs) < 20 or len(lows) < 20:
            return None

        # Check cooldown after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check market stress: avoid regime meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": market_regime})
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret  # Positive when BTC outperforms ETH materially

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Dynamic volatility confirmation: ensure ETH is not in a completely stagnant band
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0.0:
            return None
        atr_pct = (atr / ctx.bar.close) * 10000.0  # ATR in bps

        # Active position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion of spread achieved or technical target reached
            if pos_dir == "long":
                if spread < -0.5 or rsi > 68.0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_spread_reverted", "spread": spread, "rsi": rsi})
            elif pos_dir == "short":
                if spread > 0.5 or rsi < 32.0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_spread_reverted", "spread": spread, "rsi": rsi})
            return None

        # Entry logic:
        # Long ETH when BTC outruns ETH by >= 1.8%, but ETH is not overbought (RSI < 52)
        # and baseline volatility is reasonable (atr_pct >= 30 bps)
        if spread >= self.spread_threshold and rsi < 52.0 and atr_pct >= 30.0:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "atr_bps": atr_pct
                }
            )

        # Short ETH when ETH outruns BTC by >= 1.8% (spread <= -1.8%), but ETH is not deeply oversold (RSI > 48)
        if spread <= -self.spread_threshold and rsi > 48.0 and atr_pct >= 30.0:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "atr_bps": atr_pct
                }
            )

        return None