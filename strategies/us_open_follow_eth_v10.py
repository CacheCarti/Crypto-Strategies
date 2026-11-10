from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold (UTC 15 to 21)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.open_13_price: Optional[float] = None

    def _atr(self, highs, lows, closes, period=24) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        current_day = dt.toordinal()
        hour = dt.hour

        # Hard intraday session close at UTC 21:00 or later
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_utc21", "hour": hour, "close": ctx.bar.close}
                )
            return None

        # Capture opening reference price at start of US session (UTC 13:00)
        if hour == 13:
            self.open_13_price = ctx.bar.open

        # Evaluate continuation breakout strictly at UTC 15:00 close (after the first 2 hours 13-15)
        if hour == 15 and self.last_traded_day != current_day and self.open_13_price is not None:
            highs = ctx.highs(48)
            lows = ctx.lows(48)
            atr_val = self._atr(highs, lows, closes, period=24)
            if atr_val is None or atr_val <= 0:
                return None

            # Calculate recent hourly ranges to establish median baseline
            ranges = [highs[i] - lows[i] for i in range(len(highs) - 24, len(highs))]
            median_range = statistics.median(ranges)

            two_hour_net_move = ctx.bar.close - self.open_13_price
            abs_move = abs(two_hour_net_move)

            # Move must exceed median 1h range to show decisive institutional direction,
            # but not exceed 2.6x ATR to avoid buying exhaustion / blow-off moves.
            if abs_move > (median_range * 1.1) and abs_move < (atr_val * 2.6):
                # Ensure broader market regime is not in severe meltdown
                regime = ctx.market.get("regime", "NORMAL")
                if regime != "MELTDOWN":
                    self.last_traded_day = current_day
                    ratio = abs_move / atr_val
                    conf = min(0.85, max(0.55, 0.5 + 0.15 * ratio))

                    if two_hour_net_move > 0:
                        return ctx.signal(
                            "long",
                            confidence=conf,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "us_open_bullish_continuation",
                                "net_move": two_hour_net_move,
                                "median_range": median_range,
                                "atr": atr_val,
                                "hour": hour,
                            }
                        )
                    else:
                        return ctx.signal(
                            "short",
                            confidence=conf,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "us_open_bearish_continuation",
                                "net_move": two_hour_net_move,
                                "median_range": median_range,
                                "atr": atr_val,
                                "hour": hour,
                            }
                        )

        return None