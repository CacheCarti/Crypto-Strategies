from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.open_13_price = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        highs = ctx.highs(48)
        lows = ctx.lows(48)
        if len(closes) < 48:
            return None

        current_dt = ctx.bar.timestamp
        current_date = current_dt.date()
        hour = current_dt.hour

        # Session close rule: UTC 21 closes any open position regardless
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Track the 13:00 bar open price (beginning of US session)
        if hour == 13:
            self.open_13_price = ctx.bar.open

        # Evaluate continuation only at 15:00 UTC (after two full US-session hours)
        if hour != 15:
            return None

        # One trade per day restriction
        if self.last_traded_date == current_date:
            return None

        if self.open_13_price is None or self.open_13_price == 0:
            return None

        # Calculate opening 2-hour move
        move_pct = (ctx.bar.close - self.open_13_price) / self.open_13_price
        move_bps = abs(move_pct) * 10000.0

        # Calculate recent median 2-bar range over the last 24 bars
        two_bar_ranges = []
        for i in range(len(closes) - 24, len(closes), 2):
            if i >= 2:
                rng = (abs(closes[i] - closes[i - 2]) / closes[i - 2]) * 10000.0
                two_bar_ranges.append(rng)
        
        median_range = statistics.median(two_bar_ranges) if len(two_bar_ranges) >= 5 else 60.0

        # Require significant directional impulse: above median, but not overextended (< 350 bps)
        if move_bps < max(median_range * 1.1, 45.0) or move_bps > 350.0:
            return None

        # ATR & Trend filter (must align with 24-bar SMA to confirm broader market momentum)
        sma24 = sum(closes[-24:]) / 24.0
        atr = self._atr(highs, lows, closes, 14) or (ctx.bar.close * 0.01)

        # BTC return alignment to avoid idiosyncratic traps
        btc_ret = ctx.market.get("btc_return_pct", 0.0)

        if move_pct > 0 and ctx.bar.close > sma24 and btc_ret >= -1.5:
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_bps": round(move_bps, 2),
                    "median_range": round(median_range, 2),
                    "sma24": round(sma24, 2),
                    "atr": round(atr, 2),
                    "btc_ret": round(btc_ret, 2)
                }
            )

        elif move_pct < 0 and ctx.bar.close < sma24 and btc_ret <= 1.5:
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_bps": round(move_bps, 2),
                    "median_range": round(median_range, 2),
                    "sma24": round(sma24, 2),
                    "atr": round(atr, 2),
                    "btc_ret": round(btc_ret, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass