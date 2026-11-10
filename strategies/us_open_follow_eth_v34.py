from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "UsOpenFollowEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_price = 0.0

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
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Mandatory session exit at or after UTC 21
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # One trade per calendar day; entry evaluated strictly at UTC 15:00 bar close
        if current_day == self.last_traded_day:
            return None

        if current_hour != 15:
            return None

        # Cross-asset and market regime sanity check
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Opening 2-hour move (from 13:00 open to 15:00 close -> index -2 open to index -1 close)
        opens = ctx.opens(5)
        open_price_13 = opens[-2]
        close_price_15 = closes[-1]
        session_move_bps = ((close_price_15 - open_price_13) / open_price_13) * 10000.0

        # Calculate volatility threshold (ATR benchmark over 14 bars)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0.0:
            return None

        atr_bps = (atr / close_price_15) * 10000.0
        min_move_bps = atr_bps * 0.75

        # Avoid overextended moves
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Cross-asset confirmation via BTC 24h momentum
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Bullish continuation entry
        if session_move_bps > min_move_bps and session_move_bps < 350.0:
            if rsi < 70.0 and btc_ret > -1.5:
                self.last_traded_day = current_day
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=220.0,
                    take_profit_bps=380.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "us_open_bullish_continuation",
                        "session_move_bps": session_move_bps,
                        "min_move_bps": min_move_bps,
                        "rsi": rsi,
                        "btc_ret": btc_ret,
                        "atr_bps": atr_bps,
                    }
                )

        # Bearish continuation entry
        if session_move_bps < -min_move_bps and session_move_bps > -350.0:
            if rsi > 30.0 and btc_ret < 1.5:
                self.last_traded_day = current_day
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=220.0,
                    take_profit_bps=380.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "us_open_bearish_continuation",
                        "session_move_bps": session_move_bps,
                        "min_move_bps": min_move_bps,
                        "rsi": rsi,
                        "btc_ret": btc_ret,
                        "atr_bps": atr_bps,
                    }
                )

        return None