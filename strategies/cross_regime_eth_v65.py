from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,  # ~4 hours target swing
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.2  # 2.2% divergence threshold
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

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Manage existing positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            rsi = self._rsi(closes, self.rsi_period)
            
            # Mean reversion completion exits
            if pos_dir == "long" and rsi is not None and rsi > 62.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_tp_rsi_normalized", "rsi": rsi})
            elif pos_dir == "short" and rsi is not None and rsi < 38.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_tp_rsi_normalized", "rsi": rsi})
            return None

        # Cooldown guard after recent exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # High volatility / meltdown filter to avoid catching knives during macro shocks
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        
        # Cross-asset divergence spread: positive means ETH leads, negative means BTC leads
        spread = eth_ret - btc_ret
        
        rsi = self._rsi(closes, self.rsi_period)
        sma20 = self._sma(closes, 20)
        if rsi is None or sma20 is None:
            return None

        current_close = ctx.bar.close

        # ETH lagging BTC significantly -> Catch-up long setup
        # Requires ETH not to be overbought already (rsi < 48) and spread divergence > threshold
        if spread < -self.spread_threshold and rsi < 48.0 and btc_ret > -1.0:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_catchup_long",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "close": current_close
                }
            )

        # ETH outrunning BTC significantly -> Mean reversion short setup
        # Requires ETH not to be deeply oversold (rsi > 52) and spread divergence > threshold
        if spread > self.spread_threshold and rsi > 52.0 and btc_ret < 1.0:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_mean_revert_short",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "close": current_close
                }
            )

        return None