from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum hold
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.session_open_price = 0.0
        self.in_session_trade = False

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour
        current_close = ctx.bar.close

        # Track the price at the beginning of the US opening window (13:00 UTC)
        if current_hour == 13:
            self.session_open_price = ctx.bar.open

        # Mandated intraday exit: flatten any open position at or after UTC 21:00
        if current_hour >= 21 and ctx.has_position():
            self.in_session_trade = False
            return ctx.signal(
                "flat",
                confidence=1.0,
                metadata={"reason": "session_close_utc21", "hour": current_hour, "close": current_close}
            )

        # Skip entries if we already traded today, already in a position, or outside entry trigger bar (15:00 UTC)
        if current_hour != 15 or self.last_trade_day == current_day or ctx.has_position():
            return None

        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        if self.session_open_price <= 0:
            return None

        # Calculate opening 2-hour displacement
        open_displacement = current_close - self.session_open_price
        abs_disp = abs(open_displacement)

        # Baseline range against recent 14-period ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Percentile/ratio check: displacement must be decisive (>= 0.75 ATR) but not overextended (<= 2.5 ATR)
        disp_ratio = abs_disp / atr
        if disp_ratio < 0.75 or disp_ratio > 2.5:
            return None

        # Direction check aligning with short-term trend filter (SMA 20)
        sma20 = sum(closes[-20:]) / 20.0

        if open_displacement > 0 and current_close > sma20:
            self.last_trade_day = current_day
            self.in_session_trade = True
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.5 + (disp_ratio - 0.75) * 0.2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "disp_ratio": round(disp_ratio, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                    "displacement": round(open_displacement, 2)
                }
            )

        elif open_displacement < 0 and current_close < sma20:
            self.last_trade_day = current_day
            self.in_session_trade = True
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.5 + (disp_ratio - 0.75) * 0.2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "disp_ratio": round(disp_ratio, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                    "displacement": round(open_displacement, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.in_session_trade = False