# -*- coding: utf-8 -*-
"""
visualizer.py
Renders protein structures as PNG images using matplotlib (no WebGL, no CDN).
Computes mean pLDDT per protein from the PDB B-factor column and writes a
dynamic, score-specific conclusion section in the HTML report.
Requires only: matplotlib (already in your conda env)
"""

import os, base64, subprocess
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D   # noqa (registers 3d projection)

COLORS_LABEL = {
    "CHRNA7_HUMAN": "#4e9af1",
    "RIC3_CAEEL":   "#f16b6b",
    "RIC3_HUMAN":   "#6bcf6b",
}

def _plddt_color(b):
    if b >= 90: return "#0053d6"
    if b >= 70: return "#65cbf3"
    if b >= 50: return "#ffdb13"
    return "#ff7d45"

def _score_label(mean):
    if mean >= 90: return "Very high", "#0053d6"
    if mean >= 70: return "High",      "#65cbf3"
    if mean >= 50: return "Low",       "#ffdb13"
    return "Very low", "#ff7d45"


# ---------------------------------------------------------------------------
# PDB parsing
# ---------------------------------------------------------------------------

def _parse_ca(pdb_text):
    coords, bfac = [], []
    for line in pdb_text.splitlines():
        if len(line) < 66:
            continue
        if line[:4] == "ATOM" and line[12:16].strip() == "CA":
            try:
                coords.append((float(line[30:38]),
                                float(line[38:46]),
                                float(line[46:54])))
                bfac.append(float(line[60:66]))
            except ValueError:
                pass
    return (np.array(coords) if coords else None,
            np.array(bfac)   if bfac   else None)


def _merge_chunks(pdb_paths):
    all_coords, all_bfac = [], []
    z_offset = 0.0
    for path in pdb_paths:
        text = open(path, encoding="utf-8").read()
        coords, bfac = _parse_ca(text)
        if coords is None or len(coords) == 0:
            continue
        coords -= coords.mean(axis=0)
        z_span = coords[:, 2].max() - coords[:, 2].min()
        coords[:, 2] += z_offset
        z_offset += z_span + 10.0
        all_coords.append(coords)
        all_bfac.append(bfac)
    if not all_coords:
        return None, None
    return np.vstack(all_coords), np.concatenate(all_bfac)


# ---------------------------------------------------------------------------
# pLDDT statistics
# ---------------------------------------------------------------------------

def _compute_plddt_stats(pdb_paths):
    """
    Read all PDB chunks for one protein and return a dict with:
      mean, median, pct_very_high (>=90), pct_high (70-89),
      pct_low (50-69), pct_very_low (<50), n_residues
    """
    all_bfac = []
    for path in pdb_paths:
        text = open(path, encoding="utf-8").read()
        _, bfac = _parse_ca(text)
        if bfac is not None and len(bfac):
            all_bfac.extend(bfac.tolist())

    if not all_bfac:
        return None

    b = np.array(all_bfac)
    n = len(b)
    return {
        "mean":          round(float(b.mean()), 1),
        "median":        round(float(np.median(b)), 1),
        "pct_very_high": round(100 * (b >= 90).sum() / n, 1),
        "pct_high":      round(100 * ((b >= 70) & (b < 90)).sum() / n, 1),
        "pct_low":       round(100 * ((b >= 50) & (b < 70)).sum() / n, 1),
        "pct_very_low":  round(100 * (b < 50).sum() / n, 1),
        "n_residues":    n,
    }


# ---------------------------------------------------------------------------
# Dynamic conclusion builder
# ---------------------------------------------------------------------------

def _build_conclusion(plddt_stats: dict) -> str:
    """
    Given a dict of {protein_name: stats_dict}, return an HTML string
    with a fully dynamic conclusion section based on the actual scores.

    Four scenarios are handled:
      A) All high (mean >= 70 for all)          — strong structural models
      B) Mixed (some high, some low)            — partial confidence
      C) All low, biologically expected         — IDP / membrane protein
      D) All low, unexpected                    — model quality warning
    """
    if not plddt_stats:
        return ""

    means     = {k: v["mean"] for k, v in plddt_stats.items() if v}
    alpha7    = means.get("CHRNA7_HUMAN", 0)
    ric3_ce   = means.get("RIC3_CAEEL",   0)
    ric3_hu   = means.get("RIC3_HUMAN",   0)

    all_means = [v for v in means.values() if v > 0]
    overall   = round(sum(all_means) / len(all_means), 1) if all_means else 0

    # ── score badges for each protein ──────────────────────────────────────
    def badge(label, mean):
        if mean == 0:
            return ""
        slabel, color = _score_label(mean)
        return (
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:center;padding:8px 0;border-bottom:1px solid #30363d;">'
            f'<span style="font-family:monospace;font-size:0.9rem;color:#c9d1d9;">{label}</span>'
            f'<span style="display:flex;align-items:center;gap:8px;">'
            f'<span style="font-size:1.1rem;font-weight:600;color:{color};">{mean}</span>'
            f'<span style="font-size:0.75rem;color:{color};background:{color}22;'
            f'padding:2px 8px;border-radius:4px;">{slabel}</span>'
            f'</span></div>'
        )

    badges_html = "".join([
        badge("CHRNA7_HUMAN (alpha7 nAChR)", alpha7),
        badge("RIC3_CAEEL (C. elegans RIC-3)", ric3_ce),
        badge("RIC3_HUMAN (human RIC-3)", ric3_hu),
    ])

    # ── determine scenario ─────────────────────────────────────────────────
    n_high = sum(1 for m in all_means if m >= 70)
    n_low  = sum(1 for m in all_means if m < 50)

    # Scenario A: all high confidence
    if all(m >= 70 for m in all_means):
        verdict_color = "#6bcf6b"
        verdict_icon  = "&#10003;"
        verdict_text  = "High-confidence structural models"
        scenario_html = f"""
<div style="margin-top:14px;">
  <p style="font-size:0.87rem;line-height:1.75;color:#c9d1d9;">
    All three proteins show <b style="color:#6bcf6b;">high to very high pLDDT</b>
    (mean {overall}), indicating ESMFold is confident in these structures.
  </p>
  <ul style="margin:10px 0 0 18px;font-size:0.87rem;line-height:1.9;color:#c9d1d9;">
    <li>The alpha7 nAChR monomer model is reliable — transmembrane helices and the
        ligand-binding domain are well-resolved.</li>
    <li>Both RIC3_CAEEL and RIC3_HUMAN show stable folds.
        {'<b style="color:#6bcf6b;">Their similar pLDDT profiles suggest conserved structure</b>, '
         'a positive indicator that C. elegans RIC-3 may retain the fold needed to chaperone human alpha7.'
         if abs(ric3_ce - ric3_hu) < 15 else
         '<b style="color:#ffdb13;">Their pLDDT profiles differ significantly</b>, '
         'suggesting structural divergence between the two chaperones.'}</li>
    <li>These models are suitable for structural comparison (TM-align), docking
        (HADDOCK), and as starting points for complex prediction on AlphaFold3.</li>
  </ul>
</div>
<div style="margin-top:14px;padding:12px 16px;background:#6bcf6b22;border-left:3px solid #6bcf6b;border-radius:0 6px 6px 0;font-size:0.85rem;color:#c9d1d9;line-height:1.7;">
  <b style="color:#6bcf6b;">Next step:</b> Run TM-align on RIC3_CAEEL vs RIC3_HUMAN.
  TM-score &gt; 0.5 confirms conserved fold &rarr; C. elegans RIC-3 likely compatible
  with human alpha7 folding. Submit the complex to AlphaFold3:
  <a href="https://alphafoldserver.com" style="color:#4e9af1;">alphafoldserver.com</a>
</div>"""

    # Scenario B: mixed confidence
    elif n_high > 0 and n_low < len(all_means):
        verdict_color = "#ffdb13"
        verdict_icon  = "&#9888;"
        verdict_text  = "Mixed confidence — partial structural model"
        high_proteins = [k for k, m in means.items() if m >= 70]
        low_proteins  = [k for k, m in means.items() if m < 70]
        scenario_html = f"""
<div style="margin-top:14px;">
  <p style="font-size:0.87rem;line-height:1.75;color:#c9d1d9;">
    Confidence is <b style="color:#ffdb13;">mixed across the three proteins</b>
    (overall mean {overall}).
    <b style="color:#6bcf6b;">{", ".join(high_proteins)}</b> show reliable structure;
    <b style="color:#ffdb13;">{", ".join(low_proteins)}</b> show lower confidence.
  </p>
  <ul style="margin:10px 0 0 18px;font-size:0.87rem;line-height:1.9;color:#c9d1d9;">
    {"<li>Alpha7 shows good confidence — its transmembrane bundle and ligand-binding domain are modelled reliably as a monomer.</li>" if alpha7 >= 70 else "<li>Alpha7 shows low confidence as a monomer — expected, since it is a pentameric membrane protein. The model reflects disorder in the absence of membrane and partner subunits.</li>"}
    {"<li>RIC3_CAEEL has higher confidence than RIC3_HUMAN — the C. elegans chaperone may have more ordered regions, which could support its function.</li>" if ric3_ce > ric3_hu + 10 else ""}
    {"<li>RIC3_HUMAN has higher confidence than RIC3_CAEEL — this may indicate the C. elegans protein has more disordered/flexible regions relative to its human counterpart.</li>" if ric3_hu > ric3_ce + 10 else ""}
    <li>Low-confidence regions are likely intrinsically disordered — they only adopt
        structure when bound to a partner. This is common in chaperones.</li>
  </ul>
</div>
<div style="margin-top:14px;padding:12px 16px;background:#ffdb1322;border-left:3px solid #ffdb13;border-radius:0 6px 6px 0;font-size:0.85rem;color:#c9d1d9;line-height:1.7;">
  <b style="color:#ffdb13;">Recommendation:</b> Use the high-confidence regions for
  structural comparison. For the full picture, predict the alpha7 pentamer with
  AlphaFold2-Multimer, and submit alpha7 + RIC-3 as a complex to AlphaFold3:
  <a href="https://alphafoldserver.com" style="color:#4e9af1;">alphafoldserver.com</a>
</div>"""

    # Scenario C: all low — biologically expected (IDPs + membrane protein)
    elif overall < 50 or (alpha7 < 70 and ric3_ce < 70 and ric3_hu < 70):
        verdict_color = "#f16b6b"
        verdict_icon  = "&#9432;"
        verdict_text  = "Low confidence — biologically meaningful result"
        # Check if both RIC3 proteins have similarly low scores (IDP symmetry)
        ric3_symmetric = abs(ric3_ce - ric3_hu) < 15 and ric3_ce > 0 and ric3_hu > 0
        scenario_html = f"""
<div style="margin-top:14px;">
  <p style="font-size:0.87rem;line-height:1.75;color:#c9d1d9;">
    All three proteins show <b style="color:#f16b6b;">low pLDDT</b>
    (overall mean {overall}). <b>This is the expected and scientifically correct
    result</b> for these specific proteins — it is not a modelling failure.
  </p>
  <ul style="margin:10px 0 0 18px;font-size:0.87rem;line-height:1.9;color:#c9d1d9;">
    <li><b style="color:#4e9af1;">Alpha7 nAChR</b> (mean {alpha7}) —
        This is a <b>pentameric transmembrane receptor</b>. ESMFold predicts a
        single chain in water; without the membrane environment and four partner
        subunits, the model cannot be confident. The low score reflects the
        missing biological context, not a poorly folded protein.</li>
    <li><b style="color:#f16b6b;">RIC3_CAEEL</b> (mean {ric3_ce}) and
        <b style="color:#6bcf6b;">RIC3_HUMAN</b> (mean {ric3_hu}) —
        RIC-3 proteins are <b>intrinsically disordered (IDPs)</b>. Low pLDDT
        for IDPs is the <em>correct</em> prediction: these proteins genuinely
        lack a stable fold in isolation and only adopt structure upon binding
        a client protein such as alpha7.</li>
    {"<li><b style='color:#6bcf6b;'>Key finding:</b> RIC3_CAEEL and RIC3_HUMAN have <b>similar pLDDT scores</b> ({ric3_ce} vs {ric3_hu}). Equally disordered chaperones suggest <b>conserved functional mechanism</b> — a positive signal that C. elegans RIC-3 may retain the interaction motifs needed to chaperone human alpha7.</li>" if ric3_symmetric else f"<li>RIC3_CAEEL (mean {ric3_ce}) and RIC3_HUMAN (mean {ric3_hu}) differ by {abs(ric3_ce - ric3_hu):.0f} points. This divergence in disorder profile warrants closer inspection of whether the C. elegans chaperone retains the key interaction motifs.</li>"}
    <li>Any blue/teal regions visible in the structures are the genuinely
        structured domains — transmembrane helices in alpha7, coiled-coil
        regions in RIC-3. These are the functionally critical segments.</li>
  </ul>
</div>
<div style="margin-top:14px;padding:12px 16px;background:#f16b6b22;border-left:3px solid #f16b6b;border-radius:0 6px 6px 0;font-size:0.85rem;color:#c9d1d9;line-height:1.7;">
  <b style="color:#f16b6b;">Overall conclusion:</b>
  Low pLDDT does not mean C. elegans RIC-3 cannot fold human alpha7 — it means
  single-chain ESMFold cannot answer that question. The answer requires modelling
  the <b>complex</b>. Submit both sequences to
  <a href="https://alphafoldserver.com" style="color:#4e9af1;">AlphaFold3</a>
  or use AlphaFold2-Multimer for a complex prediction.
  Experimental validation (TEVC or radioligand binding in <i>Xenopus</i> oocytes)
  remains the gold standard.
</div>"""

    # Scenario D: all low — unexpected (e.g. short/weird sequences)
    else:
        verdict_color = "#888"
        verdict_icon  = "&#9888;"
        verdict_text  = "Inconclusive — review inputs"
        scenario_html = f"""
<div style="margin-top:14px;">
  <p style="font-size:0.87rem;line-height:1.75;color:#c9d1d9;">
    Mean pLDDT is low across all predictions (overall {overall}).
    This may indicate sequence or model quality issues unrelated to biology.
  </p>
  <ul style="margin:10px 0 0 18px;font-size:0.87rem;line-height:1.9;color:#c9d1d9;">
    <li>Verify the sequences are full-length canonical isoforms from UniProt.</li>
    <li>ESMFold performs best on sequences under 400 aa without transmembrane
        regions. For long or membrane-spanning proteins, AlphaFold2 with MSA
        typically gives higher confidence.</li>
    <li>Consider rerunning with AlphaFold2 via
        <a href="https://colab.research.google.com/github/sokrypton/ColabFold"
           style="color:#4e9af1;">ColabFold</a>.</li>
  </ul>
</div>"""

    return f"""
<div style="margin-top:28px;padding:20px 24px;background:#161b22;
            border:1px solid #30363d;border-radius:10px;max-width:760px;">

  <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">
    <span style="font-size:1.3rem;color:{verdict_color};">{verdict_icon}</span>
    <h3 style="font-family:monospace;font-size:1rem;color:{verdict_color};margin:0;">
      Conclusion: {verdict_text}
    </h3>
  </div>

  <div style="margin-bottom:16px;">
    {badges_html}
  </div>

  {scenario_html}

  <div style="margin-top:16px;padding-top:14px;border-top:1px solid #30363d;
              font-size:0.78rem;color:#8b949e;">
    pLDDT = predicted local distance difference test (0–100). Scores are stored
    in the B-factor column of ESMFold PDB output. Values &ge;90 = very high
    confidence; 70–89 = high; 50–69 = low; &lt;50 = very low / disordered.
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Render one protein to PNG bytes
# ---------------------------------------------------------------------------

def _render_png(label, pdb_paths):
    coords, bfac = _merge_chunks(pdb_paths)
    if coords is None:
        return None

    colors = [_plddt_color(b) for b in bfac]
    accent = COLORS_LABEL.get(label, "#f28e2b")
    n = len(coords)

    fig = plt.figure(figsize=(7, 5.5), facecolor="#0d1117")
    ax  = fig.add_subplot(111, projection="3d", facecolor="#0d1117")

    for i in range(n - 1):
        ax.plot([coords[i,0], coords[i+1,0]],
                [coords[i,1], coords[i+1,1]],
                [coords[i,2], coords[i+1,2]],
                color=colors[i], linewidth=3, alpha=0.9,
                solid_capstyle="round")

    ax.scatter(coords[:,0], coords[:,1], coords[:,2],
               c=colors, s=8, zorder=5, alpha=0.8)

    ax.text2D(0.5, 0.97, label, transform=ax.transAxes,
              ha="center", va="top", fontsize=13, fontweight="bold",
              color=accent, fontfamily="monospace")

    ax.set_axis_off()
    ax.view_init(elev=25, azim=45)

    maxspan = max(
        coords[:,0].max()-coords[:,0].min(),
        coords[:,1].max()-coords[:,1].min(),
        coords[:,2].max()-coords[:,2].min(),
    ) / 2
    mid = coords.mean(axis=0)
    ax.set_xlim(mid[0]-maxspan, mid[0]+maxspan)
    ax.set_ylim(mid[1]-maxspan, mid[1]+maxspan)
    ax.set_zlim(mid[2]-maxspan, mid[2]+maxspan)

    fig.tight_layout(pad=0.5)

    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor="#0d1117")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Agent 2 — Structure Report</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;500&display=swap');
  :root{{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;--blue:#4e9af1;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans',sans-serif;padding:36px;}}
  h1{{font-family:'IBM Plex Mono',monospace;font-size:1.5rem;color:var(--blue);margin-bottom:6px;}}
  .sub{{color:var(--muted);font-size:0.85rem;margin-bottom:32px;padding-bottom:16px;border-bottom:1px solid var(--border);}}
  .grid{{display:flex;flex-wrap:wrap;gap:24px;}}
  .card{{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;}}
  .card-header{{padding:14px 18px 10px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:baseline;}}
  .card-header h2{{font-family:'IBM Plex Mono',monospace;font-size:1rem;font-weight:600;}}
  .card-header span{{font-size:0.78rem;color:var(--muted);}}
  .card img{{display:block;}}
  .card-footer{{padding:10px 18px;font-size:0.78rem;color:var(--muted);border-top:1px solid var(--border);}}
  .legend{{margin-top:28px;padding:16px 20px;background:var(--card);border:1px solid var(--border);border-radius:10px;display:inline-flex;gap:20px;flex-wrap:wrap;font-size:0.82rem;color:var(--muted);}}
  .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle;}}
  a{{color:#4e9af1;}}
</style>
</head>
<body>
<h1>&#8594; Agent 2 &mdash; Predicted Protein Structures</h1>
<div class="sub">ESMFold &nbsp;|&nbsp; CA backbone trace &nbsp;|&nbsp; Coloured by pLDDT confidence &nbsp;|&nbsp; {timestamp}</div>

<div class="grid">{cards}</div>

<div class="legend">
  <b>pLDDT confidence:</b>
  <span><span class="dot" style="background:#0053d6"></span>Very high &gt;90</span>
  <span><span class="dot" style="background:#65cbf3"></span>High 70&ndash;90</span>
  <span><span class="dot" style="background:#ffdb13"></span>Low 50&ndash;70</span>
  <span><span class="dot" style="background:#ff7d45"></span>Very low &lt;50</span>
</div>

{conclusion}

</body>
</html>"""

_CARD = """\
<div class="card">
  <div class="card-header">
    <h2 style="color:{color}">{label}</h2>
    <span>{length} aa &nbsp;|&nbsp; mean pLDDT: <b style="color:{score_color}">{mean_plddt}</b> &nbsp;|&nbsp; {n_chunks} chunk(s)</span>
  </div>
  <img src="data:image/png;base64,{b64}" width="650" alt="{label}"/>
  <div class="card-footer">ESMFold &nbsp;|&nbsp; {files}</div>
</div>"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# Map from result key to the short_id used in plddt_stats lookup
_NAME_TO_SHORTID = {
    "human_alpha7":  "CHRNA7_HUMAN",
    "celegans_RIC3": "RIC3_CAEEL",
    "human_RIC3":    "RIC3_HUMAN",
}

def generate(sequences, structure_results, output_dir):
    from datetime import datetime
    os.makedirs(output_dir, exist_ok=True)
    cards = []
    plddt_stats = {}   # short_id -> stats dict

    for name, result in structure_results.items():
        if result.get("status") != "ok" or not result.get("pdb_paths"):
            continue
        meta   = sequences.get(name, {})
        label  = meta.get("short_id", name)
        length = meta.get("length", "?")
        color  = COLORS_LABEL.get(label, "#f28e2b")
        paths  = result["pdb_paths"]

        print(f"  Rendering {label} ({len(paths)} chunk(s)) ...", end=" ", flush=True)

        # Compute pLDDT stats
        stats = _compute_plddt_stats(paths)
        if stats:
            plddt_stats[label] = stats
            mean_plddt = stats["mean"]
        else:
            mean_plddt = 0

        score_label_text, score_color = _score_label(mean_plddt)

        png = _render_png(label, paths)
        if png is None:
            print("FAILED — no CA atoms in PDB")
            continue

        b64    = base64.b64encode(png).decode("ascii")
        fnames = ", ".join(os.path.basename(p) for p in paths)
        cards.append(_CARD.format(
            label=label, color=color, length=length,
            mean_plddt=mean_plddt, score_color=score_color,
            n_chunks=len(paths), b64=b64, files=fnames,
        ))
        print(f"OK  mean pLDDT={mean_plddt}  ({len(png)//1024} KB)")

    if not cards:
        print("  [!] Nothing to render.")
        return None

    # Print pLDDT summary to terminal
    print("\n  pLDDT summary:")
    for lbl, s in plddt_stats.items():
        sl, _ = _score_label(s["mean"])
        print(f"    {lbl:<22} mean={s['mean']:5.1f}  [{sl}]"
              f"  very_high={s['pct_very_high']}%"
              f"  high={s['pct_high']}%"
              f"  low={s['pct_low']}%"
              f"  very_low={s['pct_very_low']}%")

    conclusion_html = _build_conclusion(plddt_stats)

    html      = _HTML.format(
        timestamp  = datetime.now().strftime("%Y-%m-%d %H:%M"),
        cards      = "\n".join(cards),
        conclusion = conclusion_html,
    )
    html_path = os.path.join(output_dir, "structures_report.html")
    open(html_path, "w", encoding="utf-8").write(html)
    print(f"\n  Report    -> {html_path}")
    _open_browser(html_path)
    return html_path


def _open_browser(html_path):
    abs_path = os.path.abspath(html_path)
    win_path = None
    if abs_path.startswith("/mnt/"):
        parts    = abs_path[5:].split("/", 1)
        win_path = parts[0].upper() + ":\\" + parts[1].replace("/", "\\")

    for cmd in [
        (["explorer.exe",               win_path] if win_path else None),
        (["cmd.exe", "/c", "start", "", win_path] if win_path else None),
        ["xdg-open", abs_path],
        ["open",     abs_path],
    ]:
        if not cmd:
            continue
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            print("  Browser   -> opened automatically")
            return
        except Exception:
            continue
    print(f"  Open manually: {win_path or abs_path}")
