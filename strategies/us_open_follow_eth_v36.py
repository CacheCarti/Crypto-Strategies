from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 40 or len(highs) < 40 or len(lows) < 40:
            return None

        current_dt = ctx.bar.timestamp
        current_date = (current_dt.year, current_dt.month, current_dt.day)
        current_hour = current_dt.hour

        # Hard intraday session close at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_utc21", "hour": current_hour})
            return None

        # Entry window: exactly at UTC 15:00 after observing UTC 13:00 - 15:00
        if current_hour != 15:
            return None

        # One trade per calendar day rule
        if self.last_traded_date == current_date:
            return None

        # Calculate median 2-bar true range over the last 30 bars (15 paired windows)
        pair_ranges = []
        for i in range(12, 38, 2):
            w_high = max(highs[i:i+2])
            w_low = min(lows[i:i+2])
            pair_ranges.append(w_high - w_low)

        if not pair_ranges:
            return None

        median_range = statistics.median(pair_ranges)

        # Opening 2-hour window: bars [-2] (13:00-14:00) and [-1] (14:00-15:00)
        open_start_price = ctx.opens(3)[-2]
        open_close_price = closes[-1]
        session_high = max(highs[-2], highs[-1])
        session_low = min(lows[-2], lows[-1])
        total_session_range = session_high - session_low

        if total_session_range <= 0 or median_range <= 0:
            return None

        net_move = open_close_price - open_start_price
        range_expansion = total_session_range / median_range

        # Require meaningful expansion vs recent median range
        if range_expansion < 1.15:
            return None

        # Efficiency ratio: close-to-open net move vs full high-low span
        efficiency = abs(net_move) / total_session_range

        # Not overextended / choppy: directional move should dominate range (efficiency > 0.60)
        if efficiency < 0.60:
            return None

        # Avoid extreme volatility/meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long continuation
        if net_move > 0:
            self.last_traded_date = current_date
            confidence = min(0.9, max(0.6, 0.5 + 0.3 * efficiency))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "range_expansion": round(range_expansion, 2),
                    "efficiency": round(efficiency, 2),
                    "net_move": round(net_move, 2),
                    "median_range": round(median_range, 2)
                }
            )

        # Short continuation
        if net_move < 0:
            self.last_traded_date = current_date
            confidence = min(0.9, max(0.6, 0.5 + 0.3 * efficiency))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "range_expansion": round(range_expansion, 2),
                    "efficiency": round(efficiency, 2),
                    "net_move": round(net_move, 2),
                    "median_range": round(median_range, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass