# -*- coding: utf-8 -*-
"""
agent2.py  —  Structural Analyzer (fully automated)
=====================================================
Research: Will C. elegans RIC-3 support folding of human alpha7 nAChR?

Steps — all automatic, one command:
  1. Fetch sequences from UniProt
  2. Predict 3D structures via ESMFold API  (free, no auth)
  3. Generate interactive HTML viewer  ->  opens in your browser

Install once:
  pip install biopython

Run:
  python agent2.py
"""

import os, sys, io, json, argparse
from datetime import datetime

from sequence_fetcher    import fetch_all
from structure_predictor import predict_all
from visualizer          import generate


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--output-dir",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"),
    )
    return p.parse_args()


def main():
    args = parse_args()
    out  = args.output_dir
    os.makedirs(out, exist_ok=True)

    # UTF-8 stdout on Windows
    if hasattr(sys.stdout, "buffer") and \
       getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("\n" + "=" * 65)
    print("  AGENT 2  —  STRUCTURAL ANALYZER")
    print("  Will C. elegans RIC-3 fold human alpha7 nAChR?")
    print("=" * 65)
    print(f"  Output : {out}")
    print(f"  Time   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ── Step 1: fetch ─────────────────────────────────────────────────
    sequences = fetch_all(out)

    # ── Step 2: predict ───────────────────────────────────────────────
    results = predict_all(sequences, out)

    # ── Step 3: visualise ─────────────────────────────────────────────
    print("\n=== [3/3] Generating interactive viewer ===")
    viewer_path = generate(sequences, results, out)

    # ── Summary ───────────────────────────────────────────────────────
    ok = sum(1 for r in results.values() if r.get("status") == "ok")

    manifest = {
        "generated":  datetime.now().isoformat(),
        "sequences":  {k: {"id": v.get("uniprot_id"), "len": v.get("length"),
                            "status": v.get("status")}
                       for k, v in sequences.items()},
        "structures": {k: {"status": v.get("status"),
                            "files":  v.get("pdb_paths", [])}
                       for k, v in results.items()},
        "viewer": viewer_path,
    }
    json.dump(manifest,
              open(os.path.join(out, "agent2_manifest.json"), "w"),
              indent=2, default=str)

    print("\n" + "=" * 65)
    print(f"  Done.  {ok}/{len(results)} structures predicted.")
    print(f"  PDB files + HTML viewer in:  {out}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
