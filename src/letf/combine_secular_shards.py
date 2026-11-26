"""Combine raw paired paths, not shard-level percentiles."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd
from .secular_regime_stress import write_summary

def combine(root: Path, output: Path, expected: int):
    manifests = sorted(root.rglob('manifest.json'))
    if len(manifests) != expected:
        raise ValueError(f'Expected {expected} shards, found {len(manifests)}')
    frames, detectors, seeds = [], [], set()
    reference = None
    for manifest in manifests:
        m = json.loads(manifest.read_text())
        spec = {k:v for k,v in m.items() if k not in ('seed','paths')}
        if reference is None:
            reference = spec
        if spec != reference:
            raise ValueError('Incompatible shard settings')
        count, seed = int(m['paths']), int(m['seed'])
        block = set(range(seed,seed+count))
        if block & seeds:
            raise ValueError('Overlapping path seeds')
        seeds.update(block)
        for name, dest, key in [('paths.csv',frames,'strategy'),('detection.csv',detectors,'signal')]:
            f = pd.read_csv(manifest.parent/name)
            if f.duplicated(['regime','path',key]).any():
                raise ValueError(f'Duplicate rows in {name}')
            if set(f.path) != set(range(count)):
                raise ValueError(f'Incomplete shard {manifest.parent}')
            if not (f.groupby(['regime',key]).size()==count).all():
                raise ValueError(f'Incomplete regime/strategy pairs in {name}')
            f['path'] += seed
            dest.append(f)
    for collection, key in [(frames,'strategy'),(detectors,'signal')]:
        expected_keys = set(zip(collection[0].regime, collection[0][key]))
        for frame in collection:
            if set(zip(frame.regime,frame[key])) != expected_keys:
                raise ValueError('Shard strategy or regime set differs')
    output.mkdir(parents=True, exist_ok=True)
    result = write_summary(pd.concat(frames,ignore_index=True),
                           pd.concat(detectors,ignore_index=True),output)
    (output/'manifest.json').write_text(json.dumps(dict(reference,
        paths=len(seeds),shards=expected,seed_ranges=sorted(seeds)),indent=2))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('--expected',type=int,required=True)
    a=p.parse_args()
    combine(a.root,a.output,a.expected)

if __name__=='__main__':
    main()
