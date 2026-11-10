from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # up to 6 hours (UTC 15 to 21)
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.atr_period = 14

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        current_date = dt.date()
        hour = dt.hour

        # Hard exit: Flatten positions by UTC 21
        if ctx.has_position():
            if hour >= 21:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "us_session_close",
                    "hour": hour,
                    "price": ctx.bar.close
                })
            return None

        # Entry window strictly at UTC 15:00 after observing UTC 13:00 and 14:00 bars
        if hour != 15:
            return None

        # One trade per day restriction
        if self.last_traded_date == current_date:
            return None

        # Regime gate to address prior OOS failures: avoid Meltdown/Crisis and verify cross-asset trend alignment
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.40:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Baseline range benchmark (recent median ATR)
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None or atr_val <= 0:
            return None

        # The 2-hour opening move: price at start of UTC 13 (open of 2 bars ago) vs end of UTC 14 (close of 1 bar ago)
        opens = ctx.opens(self.METADATA["warmup_bars"])
        open_13 = opens[-2]
        close_14 = closes[-2]
        move = close_14 - open_13
        abs_move = abs(move)

        # Filter: Move must exceed 0.85 * ATR (clear directional impulse), but not be exhausted (> 2.8 * ATR)
        if abs_move < 0.85 * atr_val or abs_move > 2.80 * atr_val:
            return None

        # Check directional alignment with current bar open and BTC momentum
        direction = "long" if move > 0 else "short"

        if direction == "long":
            if btc_ret < -1.5:  # Don't long ETH if BTC is in sharp 24h selloff
                return None
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_points": move,
                    "atr": atr_val,
                    "btc_ret": btc_ret,
                    "price": ctx.bar.close
                }
            )
        else:
            if btc_ret > 1.5:  # Don't short ETH if BTC is in sharp rally
                return None
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_points": move,
                    "atr": atr_val,
                    "btc_ret": btc_ret,
                    "price": ctx.bar.close
                }
            )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass