# -*- coding: utf-8 -*-
"""Fetch protein sequences from UniProt. Stdlib urllib only."""

import os, time
from io import StringIO
from urllib.request import urlopen, Request
from urllib.error import URLError

try:
    from Bio import SeqIO
except ImportError:
    raise ImportError("Run:  pip install biopython")

PROTEINS = {
    "human_alpha7":  {"uniprot_id": "P36544", "short_id": "CHRNA7_HUMAN",
                      "description": "Human alpha7 nAChR subunit"},
    "celegans_RIC3": {"uniprot_id": "Q21375", "short_id": "RIC3_CAEEL",
                      "description": "C. elegans RIC-3 chaperone"},
    "human_RIC3":    {"uniprot_id": "Q7Z7B1", "short_id": "RIC3_HUMAN",
                      "description": "Human RIC-3 chaperone"},
}

def _get(url):
    req = Request(url, headers={"User-Agent": "agent2/3.0"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8").strip()

def fetch_all(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    results = {}
    print("\n=== [1/3] Fetching sequences from UniProt ===")
    for name, meta in PROTEINS.items():
        print(f"  {meta['short_id']} ({meta['uniprot_id']}) ...", end=" ", flush=True)
        try:
            raw = _get(f"https://rest.uniprot.org/uniprotkb/{meta['uniprot_id']}.fasta")
            record = next(SeqIO.parse(StringIO(raw), "fasta"))
            seq = str(record.seq)
            fasta = f">{meta['short_id']} {meta['description']}\n{seq}\n"
            fpath = os.path.join(output_dir, f"{meta['short_id']}.fasta")
            open(fpath, "w", encoding="utf-8").write(fasta)
            results[name] = {**meta, "sequence": seq, "length": len(seq),
                             "fasta_path": fpath, "status": "ok"}
            print(f"OK ({len(seq)} aa)")
        except Exception as e:
            print(f"FAILED: {e}")
            results[name] = {**meta, "status": "error", "error": str(e)}
    return results
