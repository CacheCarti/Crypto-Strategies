from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    """
    Weekend Seasonality Strategy on ETH 1h.
    Exploits structural weekend liquidity compression & positive drift by entering
    late Friday / early Saturday and exiting late Sunday / early Monday.
    
    Risk Management:
    - Avoids allocation trailing giveback stopout via wider, volatility-scaled risk.
    - Uses ATR-based structure stop and timed exit before traditional market opens.
    - Requires a baseline momentum filter (price > EMA50 or RSI not deeply oversold) 
      to avoid buying into cascading systemic selloffs.
    """
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 172800,  # ~48 hour hold over the weekend
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 50
        self.atr_period = 24
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period: int = 14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()  # Monday is 0, Friday is 4, Saturday is 5, Sunday is 6
        hour = bar_time.hour
        price = ctx.bar.close

        ema50 = self._ema(closes, self.ema_period)
        atr24 = self._atr(highs, lows, closes, self.atr_period)
        rsi14 = self._rsi(closes, self.rsi_period)

        if ema50 is None or atr24 is None or rsi14 is None:
            return None

        # Check existing position for exit
        if ctx.has_position():
            # Exit late Sunday (e.g. 21:00-23:00 UTC) or early Monday (00:00-02:00 UTC)
            is_sunday_exit = (weekday == 6 and hour >= 21)
            is_monday_exit = (weekday == 0 and hour <= 3)

            # Volatility or crisis breakdown exit
            crisis_score = ctx.market.get("crisis_score", 0.0)
            crisis_exit = crisis_score > 0.85

            if is_sunday_exit or is_monday_exit or crisis_exit:
                self.last_exit_bar = ctx.bar_index
                reason = "crisis_exit" if crisis_exit else "weekend_session_complete"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                        "rsi": round(rsi14, 2),
                        "crisis_score": crisis_score
                    }
                )
            return None

        # Not in position: Entry rules
        # Gate on cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check if already triggered an entry in the same weekend window
        if (ctx.bar_index - self.last_entry_bar) < 40:
            return None

        # Entry Window: Friday late evening (18:00 - 23:00 UTC) or Saturday morning (00:00 - 06:00 UTC)
        is_friday_entry = (weekday == 4 and 18 <= hour <= 23)
        is_saturday_entry = (weekday == 5 and 0 <= hour <= 6)
        in_entry_window = is_friday_entry or is_saturday_entry

        if not in_entry_window:
            return None

        # Technical filtering:
        # Avoid entering during severe down-momentum / panic crashes
        is_not_crashing = rsi14 > 33.0
        # Require price above EMA50 or RSI indicating positive/neutral consolidation
        trend_acceptable = (price >= ema50 * 0.985) and (rsi14 < 72.0)

        # Market regime check
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if is_not_crashing and trend_acceptable:
            # Dynamic stop-loss based on 2.8 * ATR
            atr_bps = (atr24 / price) * 10000.0
            sl_bps = max(350.0, min(750.0, atr_bps * 2.8))
            tp_bps = max(500.0, sl_bps * 1.35)

            confidence = 0.65
            if price > ema50:
                confidence += 0.15

            self.last_entry_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=172800,
                metadata={
                    "reason": "weekend_seasonality_momentum_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "price": price,
                    "ema50": round(ema50, 2),
                    "rsi": round(rsi14, 2),
                    "atr_bps": round(atr_bps, 2),
                    "computed_sl_bps": round(sl_bps, 1)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index