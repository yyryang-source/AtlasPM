"""Compact numeric sample columns; no per-sample dictionaries retained."""
from __future__ import annotations
from array import array
from collections.abc import Sequence
from math import isnan
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager


class DiskFeatures:
    """Append float64 rows to disk, then expose a read-only memory map."""
    def __init__(self,path):
        self.path=Path(path)
        self.writer=self.path.open('wb')
        self.count=0
        self.mapping=None
        self.itemsize=8

    def extend(self,values):
        row=array('d',values)
        row.tofile(self.writer)
        self.count+=len(row)

    def seal(self):
        import numpy as np
        self.writer.close()
        if self.count:
            self.mapping=np.memmap(self.path,mode='r',dtype=np.float64,shape=(self.count,))

    def __len__(self):
        return self.count

    def __getitem__(self,index):
        if self.mapping is None:
            raise ValueError('FEATURE_COLUMN_NOT_SEALED')
        return self.mapping[index].tolist()

    def close(self):
        if not self.writer.closed:
            self.writer.close()
        if self.mapping is not None:
            self.mapping._mmap.close()
            self.mapping=None


@contextmanager
def feature_workspace(directory):
    if directory is None:
        yield None
        return
    root=Path.cwd().resolve()/'local_data'
    folder=Path(directory).resolve()
    if root.is_symlink() or not folder.is_relative_to(root):
        raise ValueError('FEATURE_CACHE_REQUIRES_LOCAL_DATA')
    folder.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='atlaspm-panel-',dir=folder) as tmp:
        features=DiskFeatures(Path(tmp)/'features.bin')
        try:
            yield features
        finally:
            features.close()



class CompactSamples(Sequence):
    def __init__(self,dates,horizon,feature_count,feature_column=None):
        self.dates=dates
        self.horizon=horizon
        self.feature_count=feature_count
        self.identities=[]
        self.codes={}
        self.security=array("I")
        self.session=array("I")
        self.labels=array("d")
        self.features=array("d") if feature_column is None else feature_column

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
