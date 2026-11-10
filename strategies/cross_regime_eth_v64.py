from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_history = []
        self.spread_window = 24
        self.min_spread_threshold = 1.4

    def _atr_pct(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.02
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr = sum(trs[-period:]) / period
        return atr / closes[-1] if closes[-1] > 0 else 0.02

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < 30:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.spread_window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.spread_window:
            return None

        mean_spread = sum(self.spread_history) / len(self.spread_history)
        var_spread = sum((x - mean_spread) ** 2 for x in self.spread_history) / len(self.spread_history)
        std_spread = math.sqrt(var_spread) if var_spread > 1e-8 else 0.5
        spread_zscore = (spread - mean_spread) / std_spread

        rsi = self._rsi(closes, period=14)
        atr_pct = self._atr_pct(highs, lows, closes, period=14)

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean-reversion exit condition on normalized spread
            if pos_dir == "long" and spread_zscore >= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_spread_reverted", "spread_zscore": round(spread_zscore, 2), "rsi": round(rsi, 2)}
                )
            elif pos_dir == "short" and spread_zscore <= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_spread_reverted", "spread_zscore": round(spread_zscore, 2), "rsi": round(rsi, 2)}
                )
            return None

        # Cooldown guard after last exit to keep trade count well within bounds
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Volatility regime filter: avoid executing in quiet dead-zones or extreme meltdowns
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # Entry logic with statistical dispersion gating + price momentum confirmation
        # Condition Long: ETH severely lagging BTC (spread_zscore < -1.75 and spread magnitude significant)
        # Price confirmation: RSI not completely broken but displaying exhaustion (RSI < 44)
        if spread_zscore < -1.75 and spread < -self.min_spread_threshold and rsi < 44:
            conf = min(0.9, max(0.55, 0.5 + abs(spread_zscore) * 0.1))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": round(spread, 3),
                    "spread_zscore": round(spread_zscore, 2),
                    "rsi": round(rsi, 2),
                    "atr_pct": round(atr_pct, 4)
                }
            )

        # Condition Short: ETH severely outrunning BTC (spread_zscore > 1.75 and spread magnitude significant)
        # Price confirmation: RSI displaying overbought exhaustion (RSI > 56)
        if spread_zscore > 1.75 and spread > self.min_spread_threshold and rsi > 56:
            conf = min(0.9, max(0.55, 0.5 + abs(spread_zscore) * 0.1))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": round(spread, 3),
                    "spread_zscore": round(spread_zscore, 2),
                    "rsi": round(rsi, 2),
                    "atr_pct": round(atr_pct, 4)
                }
            )

        return None