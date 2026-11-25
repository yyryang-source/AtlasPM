"""Descriptive technical indicators, no directional decisions or model calls."""
from math import isfinite
from statistics import fmean, pstdev


def _ema(values, period):
    if len(values) < period:
        return []
    result = [fmean(values[:period])]
    weight = 2 / (period + 1)
    for value in values[period:]:
        result.append(result[-1] + weight * (value - result[-1]))
    return result


def metrics(bars):
    """Closed, contiguous OHLC bars supplied by validated upstream adapter."""
    if not isinstance(bars, list) or not 1 <= len(bars) <= 1000:
        raise ValueError("Invalid technical bar count")
    data = []
    for bar in bars:
        values = [float(bar[k]) for k in ("o", "h", "l", "c")]
        op, hi, lo, cl = values
        if not all(isfinite(v) for v in values) or not 0 < lo <= min(op, cl) <= max(op, cl) <= hi:
            raise ValueError("Invalid OHLC geometry")
        data.append(values)
    closes = [x[3] for x in data]
    out = {"bars": len(data), "last_closed_price": closes[-1]}
    for period in (5, 10, 20, 50, 200):
        out[f"sma_{period}"] = fmean(closes[-period:]) if len(closes) >= period else None
        ema = _ema(closes, period)
        out[f"ema_{period}"] = ema[-1] if ema else None
    if len(closes) >= 15:
        changes = [b-a for a,b in zip(closes, closes[1:])]
        gain = fmean(max(0,x) for x in changes[:14])
        loss = fmean(max(0,-x) for x in changes[:14])
        for delta in changes[14:]:
            gain = (13*gain+max(0,delta))/14
            loss = (13*loss+max(0,-delta))/14
        out["rsi_14"] = 50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100-100/(1+gain/loss)
        ranges = [max(row[1]-row[2], abs(row[1]-prev[3]), abs(row[2]-prev[3]))
                  for prev,row in zip(data,data[1:])]
        atr = fmean(ranges[:14])
        for value in ranges[14:]:
            atr = (13*atr+value)/14
        out["atr_14"] = atr
    else:
        out.update(rsi_14=None, atr_14=None)
    fast, slow = _ema(closes,12), _ema(closes,26)
    line = [a-b for a,b in zip(fast[-len(slow):],slow)] if slow else []
    signal = _ema(line,9)
    out.update(macd=line[-1] if line else None,
               macd_signal=signal[-1] if signal else None,
               macd_histogram=line[-1]-signal[-1] if signal else None)
    if len(closes) >= 20:
        mean, sd = fmean(closes[-20:]), pstdev(closes[-20:])
        out.update(bollinger_upper=mean+2*sd,bollinger_lower=mean-2*sd,
                   observed_high_20=max(x[1] for x in data[-20:]),
                   observed_low_20=min(x[2] for x in data[-20:]))
    else:
        out.update(bollinger_upper=None,bollinger_lower=None,observed_high_20=None,observed_low_20=None)
    if any(v is not None and not isfinite(v) for v in out.values()):
        raise ValueError("Nonfinite technical metric")
    return out
