"""Scoped host arrays for synthetic research; not a process-wide RSS limit."""
from __future__ import annotations
from contextlib import AbstractContextManager
from pathlib import Path
from tempfile import TemporaryDirectory
from math import prod
import shutil


class FoldArrays(AbstractContextManager):
    def __init__(self, budget_mib=None, spill_dir=None):
        if budget_mib is not None and (type(budget_mib) is not int or budget_mib<1):
            raise ValueError("INVALID_ARRAY_MEMORY_BUDGET")
        self.limit=None if budget_mib is None else budget_mib*1024**2
        self.resident=0
        self.spilled=0
        self.spill_dir=None
        if spill_dir is not None:
            root=Path.cwd().resolve()/"local_data"
            self.spill_dir=Path(spill_dir).resolve()
            if root.is_symlink() or not self.spill_dir.is_relative_to(root):
                raise ValueError("SPILL_REQUIRES_LOCAL_DATA")
            self.spill_dir.mkdir(parents=True,exist_ok=True)
        self.temp=None
        self.maps=[]
        self.count=0

    def allocate(self,shape,dtype="float64"):
        import numpy as np
        size=prod(shape)*np.dtype(dtype).itemsize
        if self.limit is None or self.resident+size<=self.limit:
            self.resident+=size
            return np.empty(shape,dtype=dtype)
        if self.spill_dir is None:
            raise ValueError("ARRAY_BUDGET_EXCEEDED_ENABLE_SPILL")
        if self.temp is None:
            self.temp=TemporaryDirectory(prefix="atlaspm-fold-",dir=self.spill_dir)
        if shutil.disk_usage(self.temp.name).free<size:
            raise ValueError("INSUFFICIENT_SPILL_DISK")
        self.count+=1
        arr=np.memmap(Path(self.temp.name)/f"{self.count}.bin",mode="w+",dtype=dtype,shape=shape)
        self.maps.append(arr)
        self.spilled+=size
        return arr

    def rows(self,rows,shape,extract,dtype="float64"):
        arr=self.allocate(shape,dtype)
        for i,row in enumerate(rows):
            arr[i]=extract(row)
        return arr

    def __exit__(self,exc_type,exc,tb):
        for arr in self.maps:
            arr.flush()
            arr._mmap.close()
        self.maps.clear()
        if self.temp is not None:
            self.temp.cleanup()
        return False
