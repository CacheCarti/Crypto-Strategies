from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceSqueeze(Strategy):
    METADATA = {
        "name": "EthConfluenceSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.max_hold_bars = 24

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 30:
            return None

        rsi = self._rsi(closes, self.period)
        atr = self._atr(highs, lows, closes, self.period)
        if rsi is None or atr is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        close_price = ctx.bar.close

        # Handle active position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Structural exit: crowd squeeze played out (RSI normalized or funding turned positive) or timed out
            if rsi >= 55.0 or funding > 0.0001 or bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalized_or_time_exit",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "fear_greed": fg_index
                    }
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Triple edge stack:
        # 1. Crowd aggressively shorting: funding_rate_ethusdt <= 0.0 (or negative)
        # 2. Macro panic: Fear & Greed < 38
        # 3. Technical oversold exhaustion: RSI(14) < 36
        if funding <= 0.0 and fg_index <= 38.0 and rsi <= 36.0:
            # Volatility-scaled dynamic stop & target
            vol_bps = (atr / close_price) * 10000.0 if close_price > 0 else 200.0
            sl_bps = max(350.0, min(600.0, vol_bps * 2.2))
            tp_bps = max(550.0, min(900.0, vol_bps * 3.5))

            # Confidence scaled by depth of fear and oversold conditions
            rsi_factor = max(0.0, (36.0 - rsi) / 20.0)
            confidence = min(0.95, 0.65 + rsi_factor)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_stack_confluence_long",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fg_index,
                    "atr_bps": round(vol_bps, 1),
                    "price": close_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index