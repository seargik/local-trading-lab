from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


_BASE_LIBRARY: list[dict[str, str]] = [
    {"indicator": "close", "category": "OHLCV main TF", "what_it_is": "Latest close on the main timeframe.", "formula": "Current candle close."},
    {"indicator": "open", "category": "OHLCV main TF", "what_it_is": "Latest open on the main timeframe.", "formula": "Current candle open."},
    {"indicator": "high", "category": "OHLCV main TF", "what_it_is": "Latest high on the main timeframe.", "formula": "Current candle high."},
    {"indicator": "low", "category": "OHLCV main TF", "what_it_is": "Latest low on the main timeframe.", "formula": "Current candle low."},
    {"indicator": "volume", "category": "OHLCV main TF", "what_it_is": "Latest traded volume.", "formula": "Current candle volume."},
    {"indicator": "returns_1", "category": "Price change", "what_it_is": "1-bar percentage return.", "formula": "close / previous_close - 1."},
    {"indicator": "returns_5", "category": "Price change", "what_it_is": "5-bar percentage return.", "formula": "close / close_5_bars_ago - 1."},
    {"indicator": "returns_20", "category": "Price change", "what_it_is": "20-bar percentage return.", "formula": "close / close_20_bars_ago - 1."},
    {"indicator": "rsi_7", "category": "Momentum", "what_it_is": "7-period RSI.", "formula": "Wilder-style RSI over close with length 7."},
    {"indicator": "rsi_14", "category": "Momentum", "what_it_is": "14-period RSI.", "formula": "Wilder-style RSI over close with length 14."},
    {"indicator": "rsi_21", "category": "Momentum", "what_it_is": "21-period RSI.", "formula": "Wilder-style RSI over close with length 21."},
    {"indicator": "rsi_slope", "category": "Momentum", "what_it_is": "RSI14 slope over 5 bars.", "formula": "rsi_14 - rsi_14.shift(5)."},
    {"indicator": "rsi_regime", "category": "Momentum", "what_it_is": "RSI regime label.", "formula": "bull_trend if RSI >= 60, bear_trend if RSI <= 40, overbought if RSI >= 70, oversold if RSI <= 30, else neutral."},
    {"indicator": "atr_14", "category": "Volatility", "what_it_is": "14-period ATR.", "formula": "EMA-like average of true range over 14 bars."},
    {"indicator": "atr_pct", "category": "Volatility", "what_it_is": "ATR expressed as a fraction of price.", "formula": "atr_14 / close."},
    {"indicator": "adx_14", "category": "Trend strength", "what_it_is": "14-period ADX.", "formula": "Wilder-style ADX from +DI and -DI over 14 bars."},
    {"indicator": "vwap_session", "category": "Mean / fair value", "what_it_is": "Running session VWAP proxy.", "formula": "Cumulative typical_price * volume / cumulative volume."},
    {"indicator": "price_above_vwap", "category": "Mean / fair value", "what_it_is": "Whether price is above VWAP.", "formula": "close > vwap_session."},
    {"indicator": "vwap_distance_pct", "category": "Mean / fair value", "what_it_is": "Distance from price to VWAP.", "formula": "(close - vwap_session) / vwap_session."},
    {"indicator": "ema_20", "category": "Trend", "what_it_is": "20-period EMA.", "formula": "EMA(close, 20)."},
    {"indicator": "ema_50", "category": "Trend", "what_it_is": "50-period EMA.", "formula": "EMA(close, 50)."},
    {"indicator": "ma_200", "category": "Trend", "what_it_is": "200-period SMA.", "formula": "SMA(close, 200)."},
    {"indicator": "distance_to_ma200_pct", "category": "Trend", "what_it_is": "Distance from price to 200 MA.", "formula": "(close - ma_200) / ma_200."},
    {"indicator": "ema_20_50_spread_pct", "category": "Trend", "what_it_is": "Distance between EMA20 and EMA50.", "formula": "(ema_20 - ema_50) / ema_50."},
    {"indicator": "ma_stack_state", "category": "Trend", "what_it_is": "MA stack label.", "formula": "bullish if close > ema20 > ema50 > ma200, bearish if reverse, else mixed."},
    {"indicator": "bb_width_pct", "category": "Volatility", "what_it_is": "Bollinger-band width as a fraction of price.", "formula": "(bb_upper - bb_lower) / close using SMA20 and 2 stdev."},
    {"indicator": "trend_distance_pct", "category": "Trend", "what_it_is": "Distance from price to EMA50.", "formula": "(close - ema_50) / ema_50."},
    {"indicator": "range_zscore", "category": "Range / mean reversion", "what_it_is": "Distance from SMA20 measured in 20-bar standard deviations.", "formula": "(close - sma_20) / std_20."},
    {"indicator": "range_position_20", "category": "Range / mean reversion", "what_it_is": "Where the close sits inside the last 20-bar range.", "formula": "(close - rolling_low_20) / (rolling_high_20 - rolling_low_20)."},
    {"indicator": "market_structure", "category": "Structure", "what_it_is": "Simple swing-structure label.", "formula": "Compares recent swing highs/lows to label HH/HL, LH/LL, mixed, etc."},
    {"indicator": "last_pivot_high", "category": "Structure", "what_it_is": "Latest pivot high level.", "formula": "Most recent confirmed pivot high from centered 5-bar swing logic."},
    {"indicator": "last_pivot_low", "category": "Structure", "what_it_is": "Latest pivot low level.", "formula": "Most recent confirmed pivot low from centered 5-bar swing logic."},
    {"indicator": "swing_high_distance_pct", "category": "Structure", "what_it_is": "Distance to latest pivot high.", "formula": "(last_pivot_high - close) / close."},
    {"indicator": "swing_low_distance_pct", "category": "Structure", "what_it_is": "Distance to latest pivot low.", "formula": "(close - last_pivot_low) / close."},
    {"indicator": "volume_ma_20", "category": "Volume", "what_it_is": "20-bar average volume.", "formula": "SMA(volume, 20)."},
    {"indicator": "volume_ratio", "category": "Volume", "what_it_is": "Current volume relative to recent average.", "formula": "volume / volume_ma_20."},
    {"indicator": "volume_spike", "category": "Volume", "what_it_is": "Boolean high-volume flag.", "formula": "volume_ratio >= 1.8."},
    {"indicator": "volume_zscore_20", "category": "Volume", "what_it_is": "Volume stretch versus recent history.", "formula": "Z-score of volume over 20 bars."},
    {"indicator": "relative_volume_same_hour", "category": "Volume", "what_it_is": "Volume versus previous bars at the same clock slot.", "formula": "volume / average historical volume for the same HH:MM slot."},
    {"indicator": "up_close_volume_ratio", "category": "Volume", "what_it_is": "Buy-dominant volume proxy.", "formula": "Rolling up-close volume / rolling down-close volume over 20 bars."},
    {"indicator": "slope_pct_10", "category": "Trend slope", "what_it_is": "10-bar EMA50 slope in percent.", "formula": "(ema_50 / ema_50.shift(10) - 1) * 100."},
    {"indicator": "bullish_divergence", "category": "Momentum / divergence", "what_it_is": "Bullish RSI divergence proxy.", "formula": "Price makes lower low while RSI makes higher low inside lookback window."},
    {"indicator": "bearish_divergence", "category": "Momentum / divergence", "what_it_is": "Bearish RSI divergence proxy.", "formula": "Price makes higher high while RSI makes lower high inside lookback window."},
    {"indicator": "liquidity_sweep_high", "category": "Liquidity / SMC proxy", "what_it_is": "Sweep of prior highs with close back below.", "formula": "high > prior rolling high and close < prior rolling high."},
    {"indicator": "liquidity_sweep_low", "category": "Liquidity / SMC proxy", "what_it_is": "Sweep of prior lows with close back above.", "formula": "low < prior rolling low and close > prior rolling low."},
    {"indicator": "fvg_bullish", "category": "SMC proxy", "what_it_is": "Bullish fair value gap proxy.", "formula": "Current low > high two bars ago."},
    {"indicator": "fvg_bearish", "category": "SMC proxy", "what_it_is": "Bearish fair value gap proxy.", "formula": "Current high < low two bars ago."},
    {"indicator": "fvg_fill_pct", "category": "SMC proxy", "what_it_is": "How much the latest gap has been filled.", "formula": "Filled portion of the latest detected FVG, from 0 to 1."},
    {"indicator": "bos_bullish", "category": "SMC proxy", "what_it_is": "Break of structure bullish proxy.", "formula": "close > prior rolling high and market_structure not bearish."},
    {"indicator": "bos_bearish", "category": "SMC proxy", "what_it_is": "Break of structure bearish proxy.", "formula": "close < prior rolling low and market_structure not bullish."},
    {"indicator": "choch_bullish", "category": "SMC proxy", "what_it_is": "Change-of-character bullish proxy.", "formula": "market_structure flips from bearish family to bullish family."},
    {"indicator": "choch_bearish", "category": "SMC proxy", "what_it_is": "Change-of-character bearish proxy.", "formula": "market_structure flips from bullish family to bearish family."},
    {"indicator": "premium_discount_zone", "category": "SMC proxy", "what_it_is": "Premium or discount relative to recent range midpoint.", "formula": "discount if close < midpoint of recent 20-bar range, premium if above, equilibrium if near midpoint."},
    {"indicator": "displacement_bullish", "category": "SMC proxy", "what_it_is": "Bullish displacement candle proxy.", "formula": "Large bullish body relative to ATR and close near high."},
    {"indicator": "displacement_bearish", "category": "SMC proxy", "what_it_is": "Bearish displacement candle proxy.", "formula": "Large bearish body relative to ATR and close near low."},
    {"indicator": "order_block_bullish_proxy", "category": "SMC proxy", "what_it_is": "Bullish order block proxy.", "formula": "Previous down candle before bullish BOS / displacement sequence."},
    {"indicator": "order_block_bearish_proxy", "category": "SMC proxy", "what_it_is": "Bearish order block proxy.", "formula": "Previous up candle before bearish BOS / displacement sequence."},
    {"indicator": "order_book_imbalance", "category": "Order book", "what_it_is": "Best-level order-book pressure proxy.", "formula": "(bid_size - ask_size) / (bid_size + ask_size), from snapshot feed."},
    {"indicator": "order_book_imbalance_5_levels", "category": "Order book", "what_it_is": "Top-5-level order-book imbalance.", "formula": "(sum_bid_qty_5 - sum_ask_qty_5) / (sum_bid_qty_5 + sum_ask_qty_5)."},
    {"indicator": "order_book_spread_bps", "category": "Order book", "what_it_is": "Bid/ask spread in basis points.", "formula": "(best_ask - best_bid) / mid_price * 10000."},
    {"indicator": "depth_wall_above_pct", "category": "Order book", "what_it_is": "Largest ask wall concentration near price.", "formula": "max top ask size / total ask size in snapshot."},
    {"indicator": "depth_wall_below_pct", "category": "Order book", "what_it_is": "Largest bid wall concentration near price.", "formula": "max top bid size / total bid size in snapshot."},
    {"indicator": "spread_regime", "category": "Order book", "what_it_is": "Spread regime label.", "formula": "narrow, normal, or wide based on spread bps thresholds."},
    {"indicator": "funding_rate", "category": "Derivatives", "what_it_is": "Latest funding rate.", "formula": "Exchange-provided funding rate snapshot."},
    {"indicator": "funding_zscore_20", "category": "Derivatives", "what_it_is": "Funding stretch versus recent history.", "formula": "Z-score of funding-rate history over 20 points."},
    {"indicator": "funding_change_1h", "category": "Derivatives", "what_it_is": "Funding change versus one hour ago.", "formula": "latest_funding - funding_value_n_bars_ago (~1h)."},
    {"indicator": "open_interest", "category": "Derivatives", "what_it_is": "Latest open interest level.", "formula": "Exchange-provided open interest snapshot."},
    {"indicator": "open_interest_value", "category": "Derivatives", "what_it_is": "Open-interest history aligned to bars.", "formula": "Exchange-provided OI history padded/aligned to candle frame."},
    {"indicator": "open_interest_zscore_20", "category": "Derivatives", "what_it_is": "Open-interest stretch versus recent average.", "formula": "Z-score of open_interest_value over 20 bars."},
    {"indicator": "oi_change_5m", "category": "Derivatives", "what_it_is": "OI change over one bar or ~5m anchor.", "formula": "open_interest_value - open_interest_value.shift(1)."},
    {"indicator": "oi_change_1h", "category": "Derivatives", "what_it_is": "OI change versus ~1h ago.", "formula": "open_interest_value - open_interest_value.shift(hour_bars)."},
    {"indicator": "price_oi_divergence", "category": "Derivatives", "what_it_is": "Price/OI divergence label.", "formula": "rising_price_falling_oi, falling_price_rising_oi, or aligned based on recent changes."},
    {"indicator": "best_bid", "category": "Order book", "what_it_is": "Best bid from latest snapshot.", "formula": "Exchange-provided best bid."},
    {"indicator": "best_ask", "category": "Order book", "what_it_is": "Best ask from latest snapshot.", "formula": "Exchange-provided best ask."},
    {"indicator": "bullish_count", "category": "HTF context", "what_it_is": "Count of higher timeframes labeled bullish/up.", "formula": "Number of HTF rows with trend = up."},
    {"indicator": "bearish_count", "category": "HTF context", "what_it_is": "Count of higher timeframes labeled bearish/down.", "formula": "Number of HTF rows with trend = down."},
    {"indicator": "local_trend", "category": "Trend labels", "what_it_is": "Simple local trend label.", "formula": "up if ema_20 > ema_50 and slope_pct_10 > 0; down if opposite; else mixed."},
    {"indicator": "global_trend", "category": "Trend labels", "what_it_is": "Longer-horizon trend label.", "formula": "above_ma200 if close > ma_200, below_ma200 if close < ma_200, else mixed."},
    {"indicator": "htf_alignment", "category": "HTF context", "what_it_is": "Combined HTF directional consensus.", "formula": "bullish if bullish HTF count > bearish count, bearish if opposite, else mixed."},
    {"indicator": "htf_15m_trend", "category": "OHLCV HTF", "what_it_is": "15m trend label.", "formula": "From 15m context row: up if ema_20 > ema_50 else down."},
    {"indicator": "htf_15m_close", "category": "OHLCV HTF", "what_it_is": "15m latest close.", "formula": "Latest close from 15m HTF frame."},
    {"indicator": "htf_15m_rsi_14", "category": "OHLCV HTF", "what_it_is": "15m RSI14.", "formula": "RSI14 calculated on 15m close."},
    {"indicator": "htf_15m_adx_14", "category": "OHLCV HTF", "what_it_is": "15m ADX14.", "formula": "ADX14 calculated on 15m candles."},
    {"indicator": "htf_15m_atr_pct", "category": "OHLCV HTF", "what_it_is": "15m ATR%.", "formula": "ATR14 / close on 15m frame."},
    {"indicator": "htf_1h_trend", "category": "OHLCV HTF", "what_it_is": "1h trend label.", "formula": "From 1h context row: up if ema_20 > ema_50 else down."},
    {"indicator": "htf_1h_close", "category": "OHLCV HTF", "what_it_is": "1h latest close.", "formula": "Latest close from 1h HTF frame."},
    {"indicator": "htf_1h_rsi_14", "category": "OHLCV HTF", "what_it_is": "1h RSI14.", "formula": "RSI14 calculated on 1h close."},
    {"indicator": "htf_1h_adx_14", "category": "OHLCV HTF", "what_it_is": "1h ADX14.", "formula": "ADX14 calculated on 1h candles."},
    {"indicator": "htf_1h_atr_pct", "category": "OHLCV HTF", "what_it_is": "1h ATR%.", "formula": "ATR14 / close on 1h frame."},
    {"indicator": "htf_4h_trend", "category": "OHLCV HTF", "what_it_is": "4h trend label.", "formula": "From 4h context row: up if ema_20 > ema_50 else down."},
    {"indicator": "htf_4h_close", "category": "OHLCV HTF", "what_it_is": "4h latest close.", "formula": "Latest close from 4h HTF frame."},
    {"indicator": "htf_4h_rsi_14", "category": "OHLCV HTF", "what_it_is": "4h RSI14.", "formula": "RSI14 calculated on 4h close."},
    {"indicator": "htf_4h_adx_14", "category": "OHLCV HTF", "what_it_is": "4h ADX14.", "formula": "ADX14 calculated on 4h candles."},
    {"indicator": "htf_4h_atr_pct", "category": "OHLCV HTF", "what_it_is": "4h ATR%.", "formula": "ATR14 / close on 4h frame."},
    {"indicator": "htf_1d_trend", "category": "OHLCV HTF", "what_it_is": "1d trend label.", "formula": "From 1d context row: up if ema_20 > ema_50 else down."},
    {"indicator": "htf_1d_close", "category": "OHLCV HTF", "what_it_is": "1d latest close.", "formula": "Latest close from 1d HTF frame."},
    {"indicator": "htf_1d_rsi_14", "category": "OHLCV HTF", "what_it_is": "1d RSI14.", "formula": "RSI14 calculated on 1d close."},
    {"indicator": "htf_1d_adx_14", "category": "OHLCV HTF", "what_it_is": "1d ADX14.", "formula": "ADX14 calculated on 1d candles."},
    {"indicator": "htf_1d_atr_pct", "category": "OHLCV HTF", "what_it_is": "1d ATR%.", "formula": "ATR14 / close on 1d frame."},
    {"indicator": "prev_day_high_distance_pct", "category": "Session / daily context", "what_it_is": "Distance to previous day high.", "formula": "(prev_day_high - close) / close."},
    {"indicator": "prev_day_low_distance_pct", "category": "Session / daily context", "what_it_is": "Distance to previous day low.", "formula": "(close - prev_day_low) / close."},
    {"indicator": "prev_day_mid_distance_pct", "category": "Session / daily context", "what_it_is": "Distance to previous day midpoint.", "formula": "(close - ((prev_day_high + prev_day_low)/2)) / close."},
    {"indicator": "day_open_distance_pct", "category": "Session / daily context", "what_it_is": "Distance to current day open.", "formula": "(close - day_open) / day_open."},
    {"indicator": "session_range_expansion_pct", "category": "Session / daily context", "what_it_is": "Current day range versus previous full day range.", "formula": "(day_high_so_far - day_low_so_far) / prev_day_range."},
    {"indicator": "breakout_above_n_bar_high", "category": "Breakout quality", "what_it_is": "Breakout above prior 20-bar high.", "formula": "close > prior rolling high over 20 bars."},
    {"indicator": "breakout_below_n_bar_low", "category": "Breakout quality", "what_it_is": "Breakout below prior 20-bar low.", "formula": "close < prior rolling low over 20 bars."},
    {"indicator": "breakout_close_strength", "category": "Breakout quality", "what_it_is": "Where close sits inside current bar.", "formula": "(close - low) / (high - low)."},
    {"indicator": "retest_success_flag", "category": "Breakout quality", "what_it_is": "Simple breakout retest success proxy.", "formula": "Recent breakout followed by hold above/below broken level."},
    {"indicator": "compression_before_breakout", "category": "Breakout quality", "what_it_is": "Breakout preceded by tight volatility regime.", "formula": "Recent BB width and ATR% below rolling medians before breakout."},
    {"indicator": "range_touch_count", "category": "Range quality", "what_it_is": "How often recent bars touched range edges.", "formula": "Count of touches near rolling 20-bar high/low over recent window."},
    {"indicator": "range_stability_score", "category": "Range quality", "what_it_is": "How stable the range boundaries are.", "formula": "1 - normalized std of rolling high/low boundaries."},
    {"indicator": "mid_reversion_speed", "category": "Range quality", "what_it_is": "Speed of reversion toward range midpoint.", "formula": "Change in distance-to-midpoint over recent bars."},
    {"indicator": "wave_leg_ratio_proxy", "category": "Wave proxy", "what_it_is": "Impulse vs retrace ratio proxy.", "formula": "abs(5-bar impulse) / abs(current retrace from latest 10-bar extreme)."},
    {"indicator": "impulse_leg_pct", "category": "Wave proxy", "what_it_is": "Latest impulse leg size.", "formula": "Maximum move from 10-bar local extreme to current close, in %."},
    {"indicator": "retracement_pct", "category": "Wave proxy", "what_it_is": "Current retracement depth from latest local extreme.", "formula": "Distance from close to latest 10-bar extreme relative to prior impulse."},
    {"indicator": "extension_ratio_1_3", "category": "Wave proxy", "what_it_is": "Wave extension proxy.", "formula": "abs(returns_20) / abs(returns_5)."},
    {"indicator": "abc_correction_depth", "category": "Wave proxy", "what_it_is": "Correction depth proxy.", "formula": "abs(close - ema_20) / atr_14."},
    {"indicator": "delta_volume_proxy", "category": "Flow proxy", "what_it_is": "Signed volume proxy.", "formula": "volume * (close - open) / max(high - low, tiny)."},
    {"indicator": "buy_sell_pressure_proxy", "category": "Flow proxy", "what_it_is": "Directional pressure proxy.", "formula": "(close - low - (high - close)) / max(high - low, tiny)."},
    {"indicator": "absorption_proxy", "category": "Flow proxy", "what_it_is": "High volume with small body / long wick proxy.", "formula": "volume_zscore high while candle body is small versus full range."},
    {"indicator": "liquidation_spike_proxy", "category": "Liquidation proxy", "what_it_is": "Flush or squeeze proxy.", "formula": "volume_zscore high, large return versus ATR, and high OI stretch."},
    {"indicator": "short_squeeze_risk", "category": "Liquidation proxy", "what_it_is": "Risk of a short squeeze.", "formula": "Negative funding, stretched OI, upward displacement, bullish order-book pressure."},
    {"indicator": "long_squeeze_risk", "category": "Liquidation proxy", "what_it_is": "Risk of a long squeeze.", "formula": "Positive funding, stretched OI, downward displacement, bearish order-book pressure."},
    {"indicator": "trend_regime_score", "category": "Regime model", "what_it_is": "Composite score for trend regime.", "formula": "Weighted mix of ADX, EMA spread, VWAP distance, HTF alignment, and MA stack."},
    {"indicator": "range_regime_score", "category": "Regime model", "what_it_is": "Composite score for range regime.", "formula": "Weighted mix of low ADX, stable range, low EMA spread, and midpoint reversion behavior."},
    {"indicator": "squeeze_regime_score", "category": "Regime model", "what_it_is": "Composite score for compression / squeeze regime.", "formula": "Weighted mix of narrow BB width, low ATR%, and low directional expansion."},
    {"indicator": "panic_regime_score", "category": "Regime model", "what_it_is": "Composite score for panic / liquidation regime.", "formula": "Weighted mix of high ATR%, high volume z-score, liquidation spike proxy, and extreme OI / funding context."},
]

INDICATOR_LIBRARY: list[dict[str, str]] = _BASE_LIBRARY
INDICATOR_CATALOG: list[str] = [row["indicator"] for row in INDICATOR_LIBRARY]
INDICATOR_SUGGESTIONS: list[dict[str, str]] = [
    {"indicator": "htf_1w_trend", "why_add": "Useful for 1h swing filtering so trades align with the weekly bias."},
    {"indicator": "liquidation_heatmap_proxy", "why_add": "Could improve squeeze and flush detection once richer derivatives data is available."},
    {"indicator": "true_cvd_proxy", "why_add": "Would improve order-flow reads when a reliable taker buy/sell feed is added."},
]


def get_indicator_library_df() -> pd.DataFrame:
    return pd.DataFrame(INDICATOR_LIBRARY)


@dataclass
class FeaturePack:
    frame: pd.DataFrame
    latest: dict[str, Any]


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_gain = up.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = down.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = true_range(df)
    atr_s = tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr_s
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    return dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean().fillna(0)


def rolling_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_pv = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return cum_pv / cum_vol


def compute_market_structure(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    swing_high = df["high"].rolling(5, center=True).max()
    swing_low = df["low"].rolling(5, center=True).min()
    labels: list[str] = []
    pivot_highs: list[float] = []
    pivot_lows: list[float] = []
    last_pivot_high: list[float | None] = []
    last_pivot_low: list[float | None] = []
    for i in range(len(df)):
        if i >= 2 and df.loc[i, "high"] >= swing_high.iloc[i]:
            pivot_highs.append(float(df.loc[i, "high"]))
            pivot_highs = pivot_highs[-2:]
        if i >= 2 and df.loc[i, "low"] <= swing_low.iloc[i]:
            pivot_lows.append(float(df.loc[i, "low"]))
            pivot_lows = pivot_lows[-2:]
        last_pivot_high.append(pivot_highs[-1] if pivot_highs else np.nan)
        last_pivot_low.append(pivot_lows[-1] if pivot_lows else np.nan)
        if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
            hh = pivot_highs[-1] > pivot_highs[-2]
            hl = pivot_lows[-1] > pivot_lows[-2]
            lh = pivot_highs[-1] < pivot_highs[-2]
            ll = pivot_lows[-1] < pivot_lows[-2]
            if hh and hl:
                labels.append("higher_high_higher_low")
            elif lh and ll:
                labels.append("lower_high_lower_low")
            elif hh and ll:
                labels.append("higher_high_lower_low")
            elif lh and hl:
                labels.append("lower_high_higher_low")
            else:
                labels.append("mixed")
        else:
            labels.append("mixed")
    return pd.Series(labels, index=df.index), pd.Series(last_pivot_high, index=df.index), pd.Series(last_pivot_low, index=df.index)


def detect_divergence(df: pd.DataFrame, rsi_series: pd.Series, lookback: int = 20) -> tuple[pd.Series, pd.Series]:
    bullish = pd.Series(False, index=df.index)
    bearish = pd.Series(False, index=df.index)
    for i in range(lookback, len(df)):
        closes = df["close"].iloc[i - lookback : i + 1]
        rsis = rsi_series.iloc[i - lookback : i + 1]
        low_idx = closes.nsmallest(2).index
        high_idx = closes.nlargest(2).index
        if len(low_idx) == 2:
            low_idx = sorted(low_idx)
            if closes.loc[low_idx[-1]] < closes.loc[low_idx[0]] and rsis.loc[low_idx[-1]] > rsis.loc[low_idx[0]]:
                bullish.iloc[i] = True
        if len(high_idx) == 2:
            high_idx = sorted(high_idx)
            if closes.loc[high_idx[-1]] > closes.loc[high_idx[0]] and rsis.loc[high_idx[-1]] < rsis.loc[high_idx[0]]:
                bearish.iloc[i] = True
    return bullish, bearish


def liquidity_sweeps(df: pd.DataFrame, lookback: int = 10) -> tuple[pd.Series, pd.Series]:
    prior_high = df["high"].shift(1).rolling(lookback).max()
    prior_low = df["low"].shift(1).rolling(lookback).min()
    sweep_high = (df["high"] > prior_high) & (df["close"] < prior_high)
    sweep_low = (df["low"] < prior_low) & (df["close"] > prior_low)
    return sweep_high.fillna(False), sweep_low.fillna(False)


def safe_zscore(series: pd.Series, lookback: int = 20) -> pd.Series:
    mean = series.rolling(lookback).mean()
    std = series.rolling(lookback).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan)


def _median_bar_minutes(out: pd.DataFrame) -> int:
    if len(out) < 2:
        return 5
    delta = out["open_time"].sort_values().diff().dropna().dt.total_seconds().median() / 60.0
    if pd.isna(delta) or delta <= 0:
        return 5
    return max(1, int(round(delta)))


def _bars_for_minutes(out: pd.DataFrame, minutes: int) -> int:
    return max(1, int(round(minutes / _median_bar_minutes(out))))


def _classify_rsi_regime(series: pd.Series) -> pd.Series:
    out = pd.Series("neutral", index=series.index)
    out = out.mask(series >= 70, "overbought")
    out = out.mask(series <= 30, "oversold")
    out = out.mask((series >= 60) & (series < 70), "bull_trend")
    out = out.mask((series > 30) & (series <= 40), "bear_trend")
    return out


def _ma_stack_state(out: pd.DataFrame) -> pd.Series:
    bullish = (out["close"] > out["ema_20"]) & (out["ema_20"] > out["ema_50"]) & (out["ema_50"] > out["ma_200"])
    bearish = (out["close"] < out["ema_20"]) & (out["ema_20"] < out["ema_50"]) & (out["ema_50"] < out["ma_200"])
    state = pd.Series("mixed", index=out.index)
    state = state.mask(bullish, "bullish")
    state = state.mask(bearish, "bearish")
    return state


def _session_context(out: pd.DataFrame) -> pd.DataFrame:
    tmp = out.copy()
    tmp["day"] = tmp["open_time"].dt.floor("D")
    day_open = tmp.groupby("day")["open"].transform("first")
    day_high_so_far = tmp.groupby("day")["high"].cummax()
    day_low_so_far = tmp.groupby("day")["low"].cummin()
    daily = tmp.groupby("day").agg(day_high=("high", "max"), day_low=("low", "min"))
    daily["prev_day_high"] = daily["day_high"].shift(1)
    daily["prev_day_low"] = daily["day_low"].shift(1)
    tmp = tmp.merge(daily[["prev_day_high", "prev_day_low"]], left_on="day", right_index=True, how="left")
    prev_mid = (tmp["prev_day_high"] + tmp["prev_day_low"]) / 2
    prev_range = (tmp["prev_day_high"] - tmp["prev_day_low"]).replace(0, np.nan)
    tmp["day_open_distance_pct"] = ((tmp["close"] - day_open) / day_open).replace([np.inf, -np.inf], np.nan)
    tmp["prev_day_high_distance_pct"] = ((tmp["prev_day_high"] - tmp["close"]) / tmp["close"]).replace([np.inf, -np.inf], np.nan)
    tmp["prev_day_low_distance_pct"] = ((tmp["close"] - tmp["prev_day_low"]) / tmp["close"]).replace([np.inf, -np.inf], np.nan)
    tmp["prev_day_mid_distance_pct"] = ((tmp["close"] - prev_mid) / tmp["close"]).replace([np.inf, -np.inf], np.nan)
    tmp["session_range_expansion_pct"] = ((day_high_so_far - day_low_so_far) / prev_range).replace([np.inf, -np.inf], np.nan)
    return tmp[["day_open_distance_pct", "prev_day_high_distance_pct", "prev_day_low_distance_pct", "prev_day_mid_distance_pct", "session_range_expansion_pct"]]


def _relative_volume_same_slot(out: pd.DataFrame) -> pd.Series:
    slot = out["open_time"].dt.strftime("%H:%M")
    baseline = out.groupby(slot)["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=3).mean())
    return (out["volume"] / baseline).replace([np.inf, -np.inf], np.nan)


def _fvg_features(out: pd.DataFrame) -> pd.DataFrame:
    bullish = out["low"] > out["high"].shift(2)
    bearish = out["high"] < out["low"].shift(2)
    gap_low = pd.Series(np.nan, index=out.index)
    gap_high = pd.Series(np.nan, index=out.index)
    gap_low = gap_low.mask(bullish, out["high"].shift(2))
    gap_high = gap_high.mask(bullish, out["low"])
    gap_low = gap_low.mask(bearish, out["high"])
    gap_high = gap_high.mask(bearish, out["low"].shift(2))
    last_gap_low = gap_low.ffill()
    last_gap_high = gap_high.ffill()
    gap_size = (last_gap_high - last_gap_low).abs().replace(0, np.nan)
    fill_amt = np.where(out["close"] < last_gap_high, (last_gap_high - out["close"]).clip(lower=0), (out["close"] - last_gap_low).clip(lower=0))
    fill_pct = pd.Series(fill_amt, index=out.index) / gap_size
    return pd.DataFrame({"fvg_bullish": bullish.fillna(False), "fvg_bearish": bearish.fillna(False), "fvg_fill_pct": fill_pct.clip(lower=0, upper=1)})


def _breakout_features(out: pd.DataFrame) -> pd.DataFrame:
    prior_high = out["high"].shift(1).rolling(20).max()
    prior_low = out["low"].shift(1).rolling(20).min()
    breakout_up = out["close"] > prior_high
    breakout_down = out["close"] < prior_low
    close_strength = ((out["close"] - out["low"]) / (out["high"] - out["low"]).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    prev_bb_med = out["bb_width_pct"].shift(1).rolling(20).median()
    prev_atr_med = out["atr_pct"].shift(1).rolling(20).median()
    compression = (out["bb_width_pct"].shift(1) < prev_bb_med) & (out["atr_pct"].shift(1) < prev_atr_med)
    retest_success = ((breakout_up.shift(1)) & (out["low"] <= prior_high) & (out["close"] >= prior_high)) | ((breakout_down.shift(1)) & (out["high"] >= prior_low) & (out["close"] <= prior_low))
    return pd.DataFrame({
        "breakout_above_n_bar_high": breakout_up.fillna(False),
        "breakout_below_n_bar_low": breakout_down.fillna(False),
        "breakout_close_strength": close_strength,
        "retest_success_flag": retest_success.fillna(False),
        "compression_before_breakout": compression.fillna(False),
    })


def _range_features(out: pd.DataFrame) -> pd.DataFrame:
    roll_high = out["high"].rolling(20).max()
    roll_low = out["low"].rolling(20).min()
    eps = (roll_high - roll_low) * 0.1
    touch_high = out["high"] >= (roll_high - eps)
    touch_low = out["low"] <= (roll_low + eps)
    touch_count = (touch_high | touch_low).rolling(20).sum()
    boundary_std = (roll_high.rolling(10).std().fillna(0) + roll_low.rolling(10).std().fillna(0)) / 2
    range_width = (roll_high - roll_low).replace(0, np.nan)
    stability = (1 - (boundary_std / range_width)).clip(lower=0, upper=1)
    mid = (roll_high + roll_low) / 2
    dist_mid = (out["close"] - mid).abs() / range_width
    mid_reversion = (dist_mid.shift(3) - dist_mid)
    return pd.DataFrame({"range_touch_count": touch_count, "range_stability_score": stability, "mid_reversion_speed": mid_reversion})


def _wave_features(out: pd.DataFrame) -> pd.DataFrame:
    recent_high = out["high"].rolling(10).max()
    recent_low = out["low"].rolling(10).min()
    impulse = ((out["close"] - recent_low) / recent_low).replace([np.inf, -np.inf], np.nan) * 100
    downside_impulse = ((recent_high - out["close"]) / recent_high).replace([np.inf, -np.inf], np.nan) * 100
    dominant_impulse = pd.Series(np.where(out["ema_20"] >= out["ema_50"], impulse, downside_impulse), index=out.index)
    retrace = ((recent_high - out["close"]) / (recent_high - recent_low).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    leg_ratio = (dominant_impulse.abs() / (retrace.abs() * 100).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    ext_ratio = (out["returns_20"].abs() / out["returns_5"].abs().replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    abc_depth = ((out["close"] - out["ema_20"]).abs() / out["atr_14"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame({
        "wave_leg_ratio_proxy": leg_ratio,
        "impulse_leg_pct": dominant_impulse,
        "retracement_pct": retrace,
        "extension_ratio_1_3": ext_ratio,
        "abc_correction_depth": abc_depth,
    })


def _smc_flow_features(out: pd.DataFrame) -> pd.DataFrame:
    prev_high20 = out["high"].shift(1).rolling(20).max()
    prev_low20 = out["low"].shift(1).rolling(20).min()
    bos_bullish = (out["close"] > prev_high20)
    bos_bearish = (out["close"] < prev_low20)
    bullish_state = out["market_structure"].isin(["higher_high_higher_low", "lower_high_higher_low"])
    bearish_state = out["market_structure"].isin(["lower_high_lower_low", "higher_high_lower_low"])
    choch_bullish = bullish_state & bearish_state.shift(1).fillna(False)
    choch_bearish = bearish_state & bullish_state.shift(1).fillna(False)
    rng_mid = (out["high"].rolling(20).max() + out["low"].rolling(20).min()) / 2
    zone = pd.Series("equilibrium", index=out.index)
    zone = zone.mask(out["close"] < rng_mid * 0.995, "discount")
    zone = zone.mask(out["close"] > rng_mid * 1.005, "premium")
    body = (out["close"] - out["open"]).abs()
    bar_range = (out["high"] - out["low"]).replace(0, np.nan)
    displacement_bullish = (out["close"] > out["open"]) & (body > out["atr_14"] * 0.8) & (((out["high"] - out["close"]) / bar_range) < 0.25)
    displacement_bearish = (out["close"] < out["open"]) & (body > out["atr_14"] * 0.8) & (((out["close"] - out["low"]) / bar_range) < 0.25)
    order_block_bullish = (out["close"].shift(1) < out["open"].shift(1)) & displacement_bullish & bos_bullish
    order_block_bearish = (out["close"].shift(1) > out["open"].shift(1)) & displacement_bearish & bos_bearish
    delta_volume = out["volume"] * ((out["close"] - out["open"]) / (bar_range.replace(0, np.nan)))
    pressure = ((out["close"] - out["low"]) - (out["high"] - out["close"])) / bar_range
    absorption = (safe_zscore(out["volume"], 20) > 1.5) & ((body / bar_range) < 0.35)
    return pd.DataFrame({
        "bos_bullish": bos_bullish.fillna(False),
        "bos_bearish": bos_bearish.fillna(False),
        "choch_bullish": choch_bullish.fillna(False),
        "choch_bearish": choch_bearish.fillna(False),
        "premium_discount_zone": zone,
        "displacement_bullish": displacement_bullish.fillna(False),
        "displacement_bearish": displacement_bearish.fillna(False),
        "order_block_bullish_proxy": order_block_bullish.fillna(False),
        "order_block_bearish_proxy": order_block_bearish.fillna(False),
        "delta_volume_proxy": delta_volume,
        "buy_sell_pressure_proxy": pressure,
        "absorption_proxy": absorption.fillna(False),
    })


def _regime_scores(out: pd.DataFrame) -> pd.DataFrame:
    def num(col: str) -> pd.Series:
        return pd.to_numeric(out.get(col), errors="coerce").fillna(0.0)

    adx = num("adx_14")
    ema_spread = num("ema_20_50_spread_pct")
    vwap_dist = num("vwap_distance_pct")
    bullish_count = num("bullish_count")
    bearish_count = num("bearish_count")
    range_stability = num("range_stability_score")
    range_touch = num("range_touch_count")
    bb_width = num("bb_width_pct")
    atr_pct = num("atr_pct")
    volume_z = num("volume_zscore_20")
    oi_z = num("open_interest_zscore_20")
    funding_z = num("funding_zscore_20")
    spread_bps = num("order_book_spread_bps")
    liq_spike = out.get("liquidation_spike_proxy", pd.Series(False, index=out.index)).fillna(False).astype(float)
    ma_stack = out.get("ma_stack_state", pd.Series("", index=out.index)).isin(["bullish", "bearish"]).astype(float)

    trend_score = (
        (adx / 40).clip(0, 1) * 35
        + ema_spread.abs().clip(0, 0.05) / 0.05 * 20
        + vwap_dist.abs().clip(0, 0.03) / 0.03 * 10
        + ma_stack * 15
        + bullish_count.clip(0, 4) / 4 * 10
        + bearish_count.clip(0, 4) / 4 * 10
    )
    range_score = (
        (1 - (adx.clip(0, 30) / 30)) * 30
        + range_stability.clip(0, 1) * 30
        + (1 - ema_spread.abs().clip(0, 0.05) / 0.05) * 20
        + range_touch.clip(0, 10) / 10 * 20
    )
    squeeze_score = (
        (1 - bb_width.clip(0, 0.08) / 0.08) * 45
        + (1 - atr_pct.clip(0, 0.05) / 0.05) * 35
        + (1 - volume_z.abs().clip(0, 3) / 3) * 20
    )
    panic_score = (
        atr_pct.clip(0, 0.08) / 0.08 * 25
        + volume_z.abs().clip(0, 4) / 4 * 20
        + liq_spike * 20
        + oi_z.abs().clip(0, 3) / 3 * 15
        + funding_z.abs().clip(0, 3) / 3 * 10
        + spread_bps.clip(0, 30) / 30 * 10
    )
    return pd.DataFrame({
        "trend_regime_score": trend_score.clip(0, 100),
        "range_regime_score": range_score.clip(0, 100),
        "squeeze_regime_score": squeeze_score.clip(0, 100),
        "panic_regime_score": panic_score.clip(0, 100),
    })


def enrich_features(df: pd.DataFrame, extras: dict[str, Any] | None = None) -> FeaturePack:
    out = df.copy().sort_values("open_time").reset_index(drop=True)
    if out.empty:
        return FeaturePack(frame=out, latest={})
    extras = extras or {}

    out["ema_20"] = ema(out["close"], 20)
    out["ema_50"] = ema(out["close"], 50)
    out["ma_200"] = sma(out["close"], 200)
    out["rsi_7"] = rsi(out["close"], 7)
    out["rsi_14"] = rsi(out["close"], 14)
    out["rsi_21"] = rsi(out["close"], 21)
    out["rsi_slope"] = out["rsi_14"] - out["rsi_14"].shift(5)
    out["rsi_regime"] = _classify_rsi_regime(out["rsi_14"])
    out["atr_14"] = atr(out, 14)
    out["adx_14"] = adx(out, 14)
    out["returns_1"] = out["close"].pct_change()
    out["returns_5"] = out["close"].pct_change(5)
    out["returns_20"] = out["close"].pct_change(20)
    out["sma_20"] = out["close"].rolling(20).mean()
    out["std_20"] = out["close"].rolling(20).std()
    out["bb_upper"] = out["sma_20"] + 2 * out["std_20"]
    out["bb_lower"] = out["sma_20"] - 2 * out["std_20"]
    out["bb_width_pct"] = ((out["bb_upper"] - out["bb_lower"]) / out["close"]).replace([np.inf, -np.inf], np.nan)
    out["atr_pct"] = (out["atr_14"] / out["close"]).replace([np.inf, -np.inf], np.nan)
    out["trend_distance_pct"] = ((out["close"] - out["ema_50"]) / out["ema_50"]).replace([np.inf, -np.inf], np.nan)
    out["distance_to_ma200_pct"] = ((out["close"] - out["ma_200"]) / out["ma_200"]).replace([np.inf, -np.inf], np.nan)
    out["ema_20_50_spread_pct"] = ((out["ema_20"] - out["ema_50"]) / out["ema_50"]).replace([np.inf, -np.inf], np.nan)
    out["range_zscore"] = ((out["close"] - out["sma_20"]) / out["std_20"]).replace([np.inf, -np.inf], np.nan)
    rolling_low_20 = out["low"].rolling(20).min()
    rolling_high_20 = out["high"].rolling(20).max()
    out["range_position_20"] = ((out["close"] - rolling_low_20) / (rolling_high_20 - rolling_low_20).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    out["volume_ma_20"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = (out["volume"] / out["volume_ma_20"]).replace([np.inf, -np.inf], np.nan)
    out["volume_spike"] = out["volume_ratio"] >= 1.8
    out["volume_zscore_20"] = safe_zscore(out["volume"], 20)
    out["relative_volume_same_hour"] = _relative_volume_same_slot(out)
    up_vol = out["volume"].where(out["close"] >= out["open"], 0.0).rolling(20).sum()
    down_vol = out["volume"].where(out["close"] < out["open"], 0.0).rolling(20).sum().replace(0, np.nan)
    out["up_close_volume_ratio"] = (up_vol / down_vol).replace([np.inf, -np.inf], np.nan)
    out["vwap_session"] = rolling_vwap(out)
    out["price_above_vwap"] = out["close"] > out["vwap_session"]
    out["vwap_distance_pct"] = ((out["close"] - out["vwap_session"]) / out["vwap_session"]).replace([np.inf, -np.inf], np.nan)
    out["slope_pct_10"] = ((out["ema_50"] / out["ema_50"].shift(10)) - 1.0) * 100
    market_structure, last_pivot_high, last_pivot_low = compute_market_structure(out)
    out["market_structure"] = market_structure
    out["last_pivot_high"] = last_pivot_high
    out["last_pivot_low"] = last_pivot_low
    out["swing_high_distance_pct"] = ((out["last_pivot_high"] - out["close"]) / out["close"]).replace([np.inf, -np.inf], np.nan)
    out["swing_low_distance_pct"] = ((out["close"] - out["last_pivot_low"]) / out["close"]).replace([np.inf, -np.inf], np.nan)
    out["ma_stack_state"] = _ma_stack_state(out)
    bull_div, bear_div = detect_divergence(out, out["rsi_14"])
    out["bullish_divergence"] = bull_div
    out["bearish_divergence"] = bear_div
    sweep_high, sweep_low = liquidity_sweeps(out)
    out["liquidity_sweep_high"] = sweep_high
    out["liquidity_sweep_low"] = sweep_low

    fvg_df = _fvg_features(out)
    for c in fvg_df.columns:
        out[c] = fvg_df[c]
    breakout_df = _breakout_features(out)
    for c in breakout_df.columns:
        out[c] = breakout_df[c]
    range_df = _range_features(out)
    for c in range_df.columns:
        out[c] = range_df[c]
    wave_df = _wave_features(out)
    for c in wave_df.columns:
        out[c] = wave_df[c]
    flow_df = _smc_flow_features(out)
    for c in flow_df.columns:
        out[c] = flow_df[c]
    sess_df = _session_context(out)
    for c in sess_df.columns:
        out[c] = sess_df[c]

    # extras and history alignment
    if extras.get("open_interest_history") is not None:
        oi_hist = pd.Series(extras.get("open_interest_history") or [], dtype=float)
        if len(oi_hist) >= len(out):
            out["open_interest_value"] = oi_hist.iloc[-len(out):].reset_index(drop=True)
        else:
            padded = pd.Series([np.nan] * (len(out) - len(oi_hist)) + oi_hist.tolist())
            out["open_interest_value"] = padded.reset_index(drop=True)
    else:
        out["open_interest_value"] = np.nan
    out["open_interest_zscore_20"] = safe_zscore(out["open_interest_value"], 20)

    if extras.get("funding_rate_history") is not None:
        fr_hist = pd.Series(extras.get("funding_rate_history") or [], dtype=float)
        if len(fr_hist) >= len(out):
            out["funding_rate_value"] = fr_hist.iloc[-len(out):].reset_index(drop=True)
        else:
            padded = pd.Series([np.nan] * (len(out) - len(fr_hist)) + fr_hist.tolist())
            out["funding_rate_value"] = padded.reset_index(drop=True)
    else:
        out["funding_rate_value"] = np.nan
    out["funding_zscore_20"] = safe_zscore(out["funding_rate_value"], 20)

    hour_bars = _bars_for_minutes(out, 60)
    out["oi_change_5m"] = out["open_interest_value"].diff(1)
    out["oi_change_1h"] = out["open_interest_value"].diff(hour_bars)
    out["funding_change_1h"] = out["funding_rate_value"].diff(hour_bars)

    price_change_1h = out["close"].pct_change(hour_bars)
    oi_change_norm = out["oi_change_1h"]
    divergence = pd.Series("aligned", index=out.index)
    divergence = divergence.mask((price_change_1h > 0) & (oi_change_norm < 0), "rising_price_falling_oi")
    divergence = divergence.mask((price_change_1h < 0) & (oi_change_norm > 0), "falling_price_rising_oi")
    out["price_oi_divergence"] = divergence

    # order book snapshot derived fields
    out["order_book_imbalance"] = extras.get("order_book_imbalance")
    out["order_book_imbalance_5_levels"] = extras.get("order_book_imbalance_5_levels")
    out["order_book_spread_bps"] = extras.get("order_book_spread_bps")
    out["depth_wall_above_pct"] = extras.get("depth_wall_above_pct")
    out["depth_wall_below_pct"] = extras.get("depth_wall_below_pct")
    spread_bps = extras.get("order_book_spread_bps")
    if spread_bps is None:
        out["spread_regime"] = "unknown"
    else:
        spread_label = "narrow" if spread_bps <= 1.5 else "normal" if spread_bps <= 4 else "wide"
        out["spread_regime"] = spread_label

    # liquidation / squeeze proxies
    out["liquidation_spike_proxy"] = (
        (out["volume_zscore_20"].fillna(0) >= 2.0)
        & ((out["returns_1"].abs() / out["atr_pct"].replace(0, np.nan)).fillna(0) >= 1.5)
        & (out["open_interest_zscore_20"].fillna(0) >= 1.0)
    )
    out["short_squeeze_risk"] = (
        (out["funding_zscore_20"].fillna(0) <= -1.0)
        & (out["open_interest_zscore_20"].fillna(0) >= 1.0)
        & out["displacement_bullish"].fillna(False)
        & ((out["order_book_imbalance"].fillna(0) > 0) | (out["order_book_imbalance_5_levels"].fillna(0) > 0))
    )
    out["long_squeeze_risk"] = (
        (out["funding_zscore_20"].fillna(0) >= 1.0)
        & (out["open_interest_zscore_20"].fillna(0) >= 1.0)
        & out["displacement_bearish"].fillna(False)
        & ((out["order_book_imbalance"].fillna(0) < 0) | (out["order_book_imbalance_5_levels"].fillna(0) < 0))
    )

    # HTF placeholder counts before summary merge
    out["bullish_count"] = np.nan
    out["bearish_count"] = np.nan

    out = out.bfill().ffill()
    latest = {k: _to_native(v) for k, v in out.iloc[-1].to_dict().items()}
    latest["funding_rate"] = _to_native(extras.get("funding_rate"))
    latest["open_interest"] = _to_native(extras.get("open_interest"))
    latest["order_book_imbalance"] = _to_native(extras.get("order_book_imbalance"))
    latest["order_book_imbalance_5_levels"] = _to_native(extras.get("order_book_imbalance_5_levels"))
    latest["order_book_spread_bps"] = _to_native(extras.get("order_book_spread_bps"))
    latest["depth_wall_above_pct"] = _to_native(extras.get("depth_wall_above_pct"))
    latest["depth_wall_below_pct"] = _to_native(extras.get("depth_wall_below_pct"))
    latest["best_bid"] = _to_native(extras.get("best_bid"))
    latest["best_ask"] = _to_native(extras.get("best_ask"))
    latest["local_trend"] = _local_trend_label(latest)
    latest["global_trend"] = _global_trend_label(latest)
    regime_df = _regime_scores(pd.DataFrame([latest]))
    for c in regime_df.columns:
        latest[c] = _to_native(regime_df.iloc[0][c])
    return FeaturePack(frame=out, latest=latest)


def summarize_htf_context(htf_feature_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    bullish = sum(1 for v in htf_feature_map.values() if (v.get("trend") == "up"))
    bearish = sum(1 for v in htf_feature_map.values() if (v.get("trend") == "down"))
    if bullish > bearish:
        return {"htf_alignment": "bullish", "bullish_count": bullish, "bearish_count": bearish}
    if bearish > bullish:
        return {"htf_alignment": "bearish", "bullish_count": bullish, "bearish_count": bearish}
    return {"htf_alignment": "mixed", "bullish_count": bullish, "bearish_count": bearish}


def build_htf_row(latest: dict[str, Any]) -> dict[str, Any]:
    trend = "up" if (latest.get("ema_20") or 0) > (latest.get("ema_50") or 0) else "down"
    return {
        "trend": trend,
        "close": _round_or_none(latest.get("close"), 10),
        "ma50": _round_or_none(latest.get("ema_50"), 10),
        "rsi_14": _round_or_none(latest.get("rsi_14"), 6),
        "adx_14": _round_or_none(latest.get("adx_14"), 6),
        "atr_pct": _round_or_none((latest.get("atr_pct") or 0) * 100, 6),
        "slope_pct": _round_or_none(latest.get("slope_pct_10"), 6),
        "market_structure": latest.get("market_structure"),
        "bullish_divergence": bool(latest.get("bullish_divergence", False)),
        "bearish_divergence": bool(latest.get("bearish_divergence", False)),
    }


def _local_trend_label(latest: dict[str, Any]) -> str:
    ema20 = latest.get("ema_20") or 0
    ema50 = latest.get("ema_50") or 0
    slope = latest.get("slope_pct_10") or 0
    if ema20 > ema50 and slope > 0:
        return "up"
    if ema20 < ema50 and slope < 0:
        return "down"
    return "mixed"


def _global_trend_label(latest: dict[str, Any]) -> str:
    close = latest.get("close") or 0
    ma200 = latest.get("ma_200") or 0
    if ma200 and close > ma200:
        return "above_ma200"
    if ma200 and close < ma200:
        return "below_ma200"
    return "mixed"


def _round_or_none(value: Any, ndigits: int) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return round(float(value), ndigits)
    except Exception:
        return value


def _to_native(value: Any) -> Any:
    if isinstance(value, (np.generic,)):
        return _to_native(value.item())
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, (np.ndarray, list, tuple)):
        return [_to_native(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_native(v) for k, v in value.items()}
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, bool):
        return bool(value)
    return value
