from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum session hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_date = None
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        ts = ctx.bar.timestamp
        current_date = ts.date()
        current_hour = ts.hour

        # 1. Mandatory Session Exit: Close positions before or at UTC 21:00
        if ctx.has_position():
            if current_hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "close": ctx.bar.close}
                )
            return None

        # 2. Cooldown and trade frequency guards
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Strictly enforce one trade per calendar day
        if self.last_trade_date == current_date:
            return None

        # Entry window: US Market Open transition (UTC 13 or 14 only)
        if current_hour not in (13, 14):
            return None

        # Volatility / Market Regime Filter
        # Skip entries if the market is in crisis / extreme shock
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(25)
        lows = ctx.lows(25)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        # 12-bar pre-session return (half-day momentum into US open)
        pre_session_ret = (closes[-1] - closes[-13]) / closes[-13]

        # Require minimum momentum relative to ATR to avoid low-vol chop
        current_price = ctx.bar.close
        min_move = (atr * 1.5) / current_price

        # Cross-asset confirmation to overcome prior single-asset momentum failure
        # Compare BTC 24h return with ETH return as broader crypto momentum confirmation
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # Long Setup: Decisive positive momentum confirmed by cross-asset strength
        if pre_session_ret > min_move and btc_ret > 0.5 and eth_ret > 0.0:
            self.last_trade_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "pre_session_ret": round(pre_session_ret, 4),
                    "min_move_threshold": round(min_move, 4),
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "atr": round(atr, 2),
                    "hour": current_hour,
                }
            )

        # Short Setup: Decisive negative momentum confirmed by cross-asset weakness
        if pre_session_ret < -min_move and btc_ret < -0.5 and eth_ret < 0.0:
            self.last_trade_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "pre_session_ret": round(pre_session_ret, 4),
                    "min_move_threshold": round(min_move, 4),
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "atr": round(atr, 2),
                    "hour": current_hour,
                }
            )

        return None