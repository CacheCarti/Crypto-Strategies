from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 10
        self.last_trade_bar = -100
        self.min_ret = 0.012  # 1.2% price move over 24h to define persistent displacement

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Mandatory cooldown between trades
        if ctx.bar_index - self.last_trade_bar < self.cooldown_period:
            return None

        # Check existing position - manage trailing / horizon
        if ctx.has_position():
            return None

        # Fetch market regime to avoid crisis / meltdown traps
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        pct_change = (current_close - past_close) / past_close

        # Immediate breakout threshold: current bar must break beyond recent 12-bar range (event boundary)
        recent_12_high = max(highs[-13:-1])
        recent_12_low = min(lows[-13:-1])

        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Bullish Squeeze: Strong 24h price rise + persistent negative funding + fresh 12h high breakout
        bull_squeeze = (
            pct_change >= self.min_ret
            and funding_rate <= -0.00003  # shorts trapped & paying or persistent discount
            and current_close > recent_12_high
        )

        # Bearish Squeeze: Strong 24h price drop + persistent positive funding + fresh 12h low breakdown
        bear_squeeze = (
            pct_change <= -self.min_ret
            and funding_rate >= 0.00003  # longs trapped & paying premium
            and current_close < recent_12_low
        )

        atr_val = self._atr(highs, lows, closes, 14)
        atr_ratio = (atr_val / current_close) if atr_val else 0.01

        # Dynamic stops scaled to volatility
        sl_bps = max(180.0, min(350.0, atr_ratio * 10000.0 * 1.5))
        tp_bps = max(350.0, min(650.0, atr_ratio * 10000.0 * 3.0))

        if bull_squeeze:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "short_funding_squeeze_breakout",
                    "ret_24h": round(pct_change, 4),
                    "funding_rate": funding_rate,
                    "price": current_close,
                    "recent_high": recent_12_high,
                },
            )

        if bear_squeeze:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "long_funding_liquidation_breakdown",
                    "ret_24h": round(pct_change, 4),
                    "funding_rate": funding_rate,
                    "price": current_close,
                    "recent_low": recent_12_low,
                },
            )

        return None