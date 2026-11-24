"""Compact numeric sample columns; no per-sample dictionaries retained."""
from __future__ import annotations
from array import array
from collections.abc import Sequence
from math import isnan


class CompactSamples(Sequence):
    def __init__(self,dates,horizon,feature_count):
        self.dates=dates
        self.horizon=horizon
        self.feature_count=feature_count
        self.identities=[]
        self.codes={}
        self.security=array("I")
        self.session=array("I")
        self.labels=array("d")
        self.features=array("d")

    def append(self,sid,signal,x,y):
        code=self.codes.get(sid)
        if code is None:
            code=len(self.identities)
            self.codes[sid]=code
            self.identities.append(sid)
        self.security.append(code)
        self.session.append(signal)
        self.labels.append(float("nan") if y is None else y)
        self.features.extend(x)

    def __len__(self):
        return len(self.session)

    def __getitem__(self,i):
        if isinstance(i,slice):
            return [self[j] for j in range(*i.indices(len(self)))]
        if i<0:
            i+=len(self)
        if not 0<=i<len(self):
            raise IndexError(i)
        signal=self.session[i]
        end=signal+self.horizon
        y=self.labels[i]
        start=i*self.feature_count
        return dict(security_id=self.identities[self.security[i]],
                    signal_index=signal,label_end_index=end,
                    date=self.dates[signal],label_end_date=self.dates[end],
                    x=list(self.features[start:start+self.feature_count]),
                    y=None if isnan(y) else y)

    def signal_at(self,i):
        return self.session[i]

    def label_exists(self,i):
        return not isnan(self.labels[i])

    @property
    def numeric_bytes(self):
        return sum(len(a)*a.itemsize for a in
                   (self.security,self.session,self.labels,self.features))


class IndexedRows(Sequence):
    """Lazy row view; avoid allocating a second list of row dictionaries."""
    def __init__(self,source,indices):
        self.source=source
        self.indices=indices

    def __len__(self):
        return len(self.indices)

    def __getitem__(self,i):
        if isinstance(i,slice):
            return IndexedRows(self.source,self.indices[i])
        return self.source[self.indices[i]]
