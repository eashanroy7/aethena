# -*- coding: utf-8 -*-
"""
Predict 3D structures via ESMFold API.
Endpoint: https://api.esmatlas.com/foldSequence/v1/pdb/
Server limit: 400 aa. Longer sequences are split into overlapping chunks.
"""

import os, time
from urllib.request import urlopen, Request
from urllib.error import URLError

ESMFOLD_URL = "https://api.esmatlas.com/foldSequence/v1/pdb/"
MAX_LEN     = 400


def _chunks(seq, max_len=MAX_LEN, overlap=20):
    """Split sequence into overlapping chunks of <= max_len."""
    if len(seq) <= max_len:
        return [("", seq)]
    parts, step, i, idx = [], max_len - overlap, 0, 1
    while i < len(seq):
        end = min(i + max_len, len(seq))
        parts.append((f"_part{idx}", seq[i:end]))
        if end == len(seq):
            break
        i += step; idx += 1
    return parts


def _post_sequence(seq, retries=3):
    """POST one sequence to ESMFold. Returns PDB text or raises."""
    req = Request(ESMFOLD_URL, data=seq.encode(),
                  headers={"Content-Type": "application/x-www-form-urlencoded",
                            "User-Agent": "agent2/3.0"},
                  method="POST")
    for attempt in range(1, retries + 1):
        try:
            with urlopen(req, timeout=180) as r:
                pdb = r.read().decode("utf-8")
            if "ATOM" not in pdb:
                raise ValueError(f"Not a PDB response: {pdb[:200]}")
            return pdb
        except (URLError, OSError) as e:
            print(f"    attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(10)
    raise RuntimeError(f"ESMFold failed after {retries} attempts")


def predict_all(sequences, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    results = {}
    print("\n=== [2/3] Predicting structures — ESMFold API ===")
    print(f"  {ESMFOLD_URL}\n")

    for name, data in sequences.items():
        if data.get("status") != "ok":
            results[name] = {"status": "skipped", "pdb_paths": []}
            continue

        label  = data["short_id"]
        seq    = data["sequence"]
        parts  = _chunks(seq)
        paths  = []
        failed = False

        print(f"  {label} ({len(seq)} aa, {len(parts)} chunk(s))")
        for suffix, chunk in parts:
            tag = f"{label}{suffix}"
            print(f"    {tag} ({len(chunk)} aa) ...", end=" ", flush=True)
            try:
                pdb  = _post_sequence(chunk)
                path = os.path.join(output_dir, f"{tag}_esmfold.pdb")
                open(path, "w", encoding="utf-8").write(pdb)
                n_atoms = pdb.count("\nATOM")
                print(f"OK ({n_atoms} atoms)  ->  {os.path.basename(path)}")
                paths.append(path)
                if suffix:          # be polite between chunks
                    time.sleep(3)
            except Exception as e:
                print(f"FAILED: {e}")
                failed = True
                break

        results[name] = {
            "label":     label,
            "pdb_paths": paths,
            "status":    "failed" if failed else "ok",
        }

    return results
