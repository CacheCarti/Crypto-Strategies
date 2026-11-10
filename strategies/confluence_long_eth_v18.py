from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceSqueeze(Strategy):
    METADATA = {
        "name": "EthConfluenceSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,  # ~8 hour hold target
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.max_hold_bars = 36
        self.entry_bar = -100
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.rsi_period + 2)
        highs = ctx.highs(self.rsi_period + 2)
        lows = ctx.lows(self.rsi_period + 2)

        if len(closes) < self.rsi_period + 2:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Structural exit: RSI rebounded into neutral/overbought or crowd flips back to aggressive long
            rsi_normalized = rsi >= 55.0
            crowd_normalized = funding > 0.00015
            timed_exit = bars_held >= self.max_hold_bars

            if rsi_normalized or crowd_normalized or timed_exit:
                self.last_exit_bar = ctx.bar_index
                reason = "timed_exit" if timed_exit else ("rsi_normalized" if rsi_normalized else "crowd_normalized")
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "fg_index": fg_index
                    }
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Confluence Entry Conditions:
        # 1. Price oversold (RSI <= 38 to allow robust confluence matching on 1h)
        # 2. Negative or zero funding rate (crowd heavily short or hedging spot)
        # 3. Macro sentiment depressed (Fear & Greed <= 38)
        cond_rsi = rsi <= 38.0
        cond_funding = funding <= 0.00001
        cond_fg = fg_index <= 38.0

        if cond_rsi and cond_funding and cond_fg:
            # Dynamic volatility-based SL/TP to solve OOS giveback-stop failure
            atr = self._atr(highs, lows, closes, 14)
            current_price = ctx.bar.close
            
            if atr and current_price > 0:
                atr_bps = (atr / current_price) * 10000.0
                dynamic_sl = max(250.0, min(500.0, atr_bps * 2.5))
                dynamic_tp = max(350.0, min(800.0, dynamic_sl * 1.8))
            else:
                dynamic_sl = 350.0
                dynamic_tp = 600.0

            # Confidence based on degree of extremity
            conf = 0.65
            if rsi < 30.0:
                conf += 0.15
            if funding < -0.00005:
                conf += 0.10
            conf = min(0.95, conf)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_short_squeeze_entry",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fg_index,
                    "atr_bps": round(dynamic_sl / 2.5, 1)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index