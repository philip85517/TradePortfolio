"""Bounded-memory fingerprints and file revisions for reusable inspections."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd


def frame_fingerprint(frame, *, cancelled=lambda:False, progress=None):
    columns=sorted(frame.columns)
    digest=hashlib.sha256()
    digest.update(json.dumps({'version':'pandas-rowhash-v1','pandas':pd.__version__,
                              'schema':[(str(c),str(frame[c].dtype)) for c in columns],
                              'rows':len(frame)},sort_keys=True).encode())
    chunks=[]
    for start in range(0,max(1,len(frame)),25000):
        if cancelled():
            raise InterruptedError('已取消数据指纹计算')
        chunk=frame.iloc[start:start+25000][columns]
        chunks.append(pd.util.hash_pandas_object(chunk,index=False).to_numpy(dtype='uint64'))
        if progress:
            progress(f'生成数据指纹 {min(start+25000,len(frame))}/{len(frame)} 行')
    # Sorting hashes makes row storage order irrelevant, preserving duplicate counts.
    hashes=np.concatenate(chunks) if chunks else np.array([],dtype='uint64')
    hashes.sort()
    digest.update(hashes.astype('<u8',copy=False).tobytes())
    return digest.hexdigest()


def file_revisions(paths):
    result=[]
    for path in sorted({Path(p).expanduser().resolve() for p in paths}):
        try:
            stat=path.stat()
            result.append((str(path),stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
        except FileNotFoundError:
            result.append((str(path),None))
    return result
