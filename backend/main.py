from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urljoin

import requests
import trafilatura
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from lxml import html as lxml_html
from pydantic import BaseModel

# Load .env from the backend package directory (not the shell's cwd), so
# `fastapi dev` / uvicorn from the repo root still see OPENAI_API_KEY, etc.
BACKEND_DIR = Path(__file__).resolve().parent
# override=True: values in backend/.env win over an empty `export OPENAI_API_KEY=`
load_dotenv(BACKEND_DIR / ".env", override=True)

app = FastAPI(title="Aethena Evidence Backend")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _resolve_db_path() -> str:
    raw = os.getenv("EVIDENCE_DB_PATH", "./evidence.db").strip() or "./evidence.db"
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = BACKEND_DIR / candidate
    return str(candidate.resolve())


EVIDENCE_DB_PATH = _resolve_db_path()
OPENALEX_USER_AGENT = os.getenv(
    "OPENALEX_USER_AGENT", "https://github.com/eashanroy7/aethena"
)
PAPER_FETCH_DELAY_SECONDS = _env_float("PAPER_FETCH_DELAY_SECONDS", 0.2)
OPENALEX_PER_PAGE_MULTIPLIER = _env_int("OPENALEX_PER_PAGE_MULTIPLIER", 5)
OPENALEX_TIMEOUT_SECONDS = _env_int("OPENALEX_TIMEOUT_SECONDS", 30)
PMC_TIMEOUT_SECONDS = _env_int("PMC_TIMEOUT_SECONDS", 30)
MAX_PAPERS_DEFAULT = _env_int("MAX_PAPERS_DEFAULT", 10)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
# Reasoning / thinking models: o3-mini, o1, gpt-4o, etc.
OPENAI_AGENT1_MODEL = os.getenv("OPENAI_AGENT1_MODEL", "o3-mini")
OPENAI_AGENT2_MODEL = os.getenv("OPENAI_AGENT2_MODEL", "gpt-4o-mini")
OPENAI_AGENT4_MODEL = os.getenv("OPENAI_AGENT4_MODEL", "gpt-4o-mini")
ESMFOLD_URL = os.getenv("ESMFOLD_URL", "https://api.esmatlas.com/foldSequence/v1/pdb/")
ESMFOLD_MAX_LEN = _env_int("ESMFOLD_MAX_LEN", 400)
ESMFOLD_OVERLAP = _env_int("ESMFOLD_OVERLAP", 20)

PROTEIN_TARGETS = [
    {
        "id": "chrna7-human",
        "name": "Human alpha7 nAChR",
        "uniprot_id": "P36544",
    },
    {
        "id": "ric3-celegans",
        "name": "C. elegans RIC-3",
        "uniprot_id": "Q21375",
    },
    {
        "id": "ric3-human",
        "name": "Human RIC-3",
        "uniprot_id": "Q7Z7B1",
    },
]


class PaperSummary(BaseModel):
    pmcid: str
    openalex_id: str | None = None
    title: str
    source_url: str
    preview: str
    fetched_at: str


class PaperDetail(PaperSummary):
    text: str
    text_sha256: str
    figures: list[dict[str, str]]
    tables: list[dict[str, Any]]


class LiteratureReferenceIn(BaseModel):
    title: str
    authors: str | None = None
    year: int | None = None
    url: str
    snippet: str | None = None
    source: str | None = None


class LiteratureQCIn(BaseModel):
    novelty: str
    summary: str
    references: list[LiteratureReferenceIn]


class HypothesesRequest(BaseModel):
    question: str
    literature: LiteratureQCIn


class HypothesisItem(BaseModel):
    id: str
    title: str
    description: str
    rationale: str


class ProteinModelOut(BaseModel):
    id: str
    name: str
    uniprotId: str
    length: int
    meanPlddt: float
    confidenceLabel: str
    summary: str
    structureSvgDataUri: str | None = None


class HypothesesResponse(BaseModel):
    hypotheses: list[HypothesisItem]
    proteinModels: list[ProteinModelOut]
    agent3Used: bool
    sourcesReviewed: int


class ExperimentPlanRequest(BaseModel):
    question: str
    literature: LiteratureQCIn
    proteinModels: list[ProteinModelOut] | None = None
    plannerContext: dict[str, Any] | None = None


@contextmanager
def db_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(EVIDENCE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS papers (
              id           INTEGER PRIMARY KEY,
              pmcid        TEXT UNIQUE NOT NULL,
              openalex_id  TEXT,
              title        TEXT,
              source_url   TEXT,
              text         TEXT,
              text_sha256  TEXT,
              figures_json TEXT,
              tables_json  TEXT,
              fetched_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS paper_queries (
              pmcid       TEXT NOT NULL,
              query       TEXT NOT NULL,
              fetched_at  TEXT NOT NULL,
              PRIMARY KEY (pmcid, query)
            );

            CREATE INDEX IF NOT EXISTS idx_paper_queries_query
              ON paper_queries(query);
            """
        )
        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        if "figures_json" not in existing_columns:
            conn.execute("ALTER TABLE papers ADD COLUMN figures_json TEXT")
        if "tables_json" not in existing_columns:
            conn.execute("ALTER TABLE papers ADD COLUMN tables_json TEXT")
        conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_pmcid(raw: str | None) -> str | None:
    if not raw:
        return None
    normalized = raw.strip()
    if normalized.isdigit():
        return normalized
    match = re.search(r"PMC(\d+)", raw, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1)


def extract_pmcid_from_work(work: dict[str, Any]) -> str | None:
    ids = work.get("ids", {}) or {}
    for candidate in [
        ids.get("pmcid"),
        ids.get("pmid"),
        (work.get("open_access", {}) or {}).get("oa_url"),
    ]:
        pmcid = parse_pmcid(candidate)
        if pmcid:
            return pmcid

    locations = work.get("locations") or []
    for location in locations:
        if not isinstance(location, dict):
            continue
        for key in ("landing_page_url", "pdf_url", "id"):
            pmcid = parse_pmcid(location.get(key))
            if pmcid:
                return pmcid
    return None


def _openalex_fetch_works_page(
    search_query: str, page: int, per_page: int
) -> list[dict[str, Any]]:
    response = requests.get(
        "https://api.openalex.org/works",
        params={
            "search": search_query,
            "filter": "open_access.is_oa:true",
            "per_page": per_page,
            "page": page,
            "select": "id,title,ids,open_access,locations",
        },
        headers={"User-Agent": OPENALEX_USER_AGENT},
        timeout=OPENALEX_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json().get("results", [])


def find_papers_merged(search_queries: list[str], n: int) -> list[dict[str, str]]:
    """Run OpenAlex in relevance order: try each search string, dedupe PMCIDs, stop at n."""
    per_page = min(max(n * OPENALEX_PER_PAGE_MULTIPLIER, 50), 200)
    max_pages = 5
    papers: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_q in search_queries:
        q = (raw_q or "").strip()
        if not q:
            continue
        for page in range(1, max_pages + 1):
            works = _openalex_fetch_works_page(q, page, per_page)
            if not works:
                break
            for work in works:
                pmcid = extract_pmcid_from_work(work)
                if not pmcid or pmcid in seen:
                    continue
                seen.add(pmcid)
                papers.append(
                    {
                        "pmcid": pmcid,
                        "title": (work.get("title") or f"PMC{pmcid}").strip(),
                        "openalex_id": (work.get("id") or "").strip(),
                        "source_url": f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{pmcid}/",
                    }
                )
                if len(papers) >= n:
                    return papers
    return papers


def find_papers(query: str, n: int) -> list[dict[str, str]]:
    return find_papers_merged([query], n)


def _parse_json_object_loose(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def agent1_research_queries(user_query: str) -> dict[str, Any]:
    """
    One OpenAI (thinking) call: rewrite for OpenAlex + up to 2 alternates.
    Falls back to raw user text if no key or API error.
    """
    clean = user_query.strip()
    base: dict[str, Any] = {
        "primary": clean,
        "alternates": [],
        "rationale": "",
        "used_openai": False,
    }
    if not OPENAI_API_KEY or len(clean) < 3:
        base["rationale"] = "OPENAI_API_KEY not set or query too short; using your text as-is."
        return base

    prompt = f"""The user is searching open-access full-text papers indexed in OpenAlex/PMC for biomedical or life-science work.

User question (verbatim):
{clean!r}

Task: Produce search strings (not a paragraph) to find the ~10 most relevant papers.

Respond with ONLY valid JSON (no markdown):
{{
  "primary": "short OpenAlex-optimized search string, English, <= 20 words",
  "alternates": ["optional second search string", "optional third search string"],
  "rationale": "one short sentence: how you interpreted the question"
}}

Rules: alternates has at most 2 strings; use [] if a single string is enough. If the phrasing is ambiguous, pick the most likely scientific reading."""

    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        completion = client.chat.completions.create(
            model=OPENAI_AGENT1_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1000,
        )
    except Exception as exc:  # noqa: BLE001 — surface any SDK/network error
        base["rationale"] = f"OpenAI call failed: {exc!s}"[:500]
        return base

    raw = (completion.choices[0].message.content or "").strip() if completion.choices else ""
    if not raw:
        base["rationale"] = "Model returned no text; using your question as the search string."
        return base

    data = _parse_json_object_loose(raw)
    if not data:
        base["rationale"] = "Could not parse model JSON; using your question as the search string."
        return base

    primary = (data.get("primary") or clean).strip() or clean
    alts = data.get("alternates")
    if isinstance(alts, str):
        alts = [alts]
    if not isinstance(alts, list):
        alts = []
    alts = [a.strip() for a in alts if isinstance(a, str) and a.strip()][:2]
    rationale = (data.get("rationale") or "").strip()

    return {
        "primary": primary,
        "alternates": alts,
        "rationale": rationale,
        "used_openai": True,
    }


def _needs_agent3(question: str) -> bool:
    return bool(
        re.search(
            r"\b(protein|fold|folding|structure|alpha7|n?achr|ric-?3|receptor|enzyme)\b",
            question,
            flags=re.IGNORECASE,
        )
    )


def _query_paper_context(question: str, limit: int = 10) -> list[dict[str, str]]:
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.pmcid, p.title, p.source_url, p.text, p.fetched_at
            FROM papers p
            JOIN paper_queries q ON q.pmcid = p.pmcid
            WHERE q.query = ?
            ORDER BY q.fetched_at DESC
            LIMIT ?
            """,
            (question, limit),
        ).fetchall()
    out: list[dict[str, str]] = []
    for row in rows:
        text = row["text"] or ""
        out.append(
            {
                "pmcid": row["pmcid"],
                "title": row["title"] or f"PMC{row['pmcid']}",
                "source_url": row["source_url"] or "",
                "preview": preview_text(text, size=220),
            }
        )
    return out


def _chunks(seq: str, max_len: int, overlap: int) -> list[str]:
    if len(seq) <= max_len:
        return [seq]
    step = max(1, max_len - overlap)
    parts: list[str] = []
    i = 0
    while i < len(seq):
        end = min(i + max_len, len(seq))
        parts.append(seq[i:end])
        if end == len(seq):
            break
        i += step
    return parts


def _parse_fasta_sequence(fasta_text: str) -> str:
    lines = [line.strip() for line in fasta_text.splitlines() if line.strip()]
    if not lines:
        return ""
    if lines[0].startswith(">"):
        lines = lines[1:]
    return "".join(lines).strip()


def _fetch_uniprot_sequence(uniprot_id: str) -> str:
    resp = requests.get(
        f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.fasta",
        timeout=30,
        headers={"User-Agent": OPENALEX_USER_AGENT},
    )
    resp.raise_for_status()
    seq = _parse_fasta_sequence(resp.text)
    if not seq:
        raise ValueError(f"Empty UniProt FASTA for {uniprot_id}")
    return seq


def _esmfold_predict_pdb(sequence: str) -> str:
    response = requests.post(
        ESMFOLD_URL,
        data=sequence.encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": OPENALEX_USER_AGENT,
        },
        timeout=180,
    )
    response.raise_for_status()
    pdb_text = response.text
    if "ATOM" not in pdb_text:
        raise ValueError("ESMFold response did not contain PDB atom records")
    return pdb_text


def _parse_ca_atoms_from_pdb(pdb_text: str) -> list[tuple[float, float, float, float]]:
    atoms: list[tuple[float, float, float, float]] = []
    for line in pdb_text.splitlines():
        if len(line) < 66:
            continue
        if not line.startswith("ATOM"):
            continue
        atom_name = line[12:16].strip()
        if atom_name != "CA":
            continue
        try:
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
            b = float(line[60:66])
        except ValueError:
            continue
        atoms.append((x, y, z, b))
    return atoms


def _confidence_label(mean_plddt: float) -> str:
    if mean_plddt >= 90:
        return "Very high"
    if mean_plddt >= 70:
        return "High"
    if mean_plddt >= 50:
        return "Low to medium"
    return "Very low"


def _atoms_to_svg_data_uri(atoms: list[tuple[float, float, float, float]]) -> str | None:
    if len(atoms) < 2:
        return None
    width, height = 420, 240
    margin = 14
    xs = [a[0] for a in atoms]
    ys = [a[1] for a in atoms]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    scale_x = (width - 2 * margin) / span_x
    scale_y = (height - 2 * margin) / span_y
    scale = min(scale_x, scale_y)

    def project(x: float, y: float) -> tuple[float, float]:
        px = margin + (x - min_x) * scale
        py = height - margin - (y - min_y) * scale
        return px, py

    def color_for_b(b: float) -> str:
        if b >= 90:
            return "#0053d6"
        if b >= 70:
            return "#65cbf3"
        if b >= 50:
            return "#ffdb13"
        return "#ff7d45"

    line_segments: list[str] = []
    for i in range(len(atoms) - 1):
        x1, y1 = project(atoms[i][0], atoms[i][1])
        x2, y2 = project(atoms[i + 1][0], atoms[i + 1][1])
        line_segments.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{color_for_b(atoms[i][3])}" stroke-width="2.1" stroke-linecap="round" />'
        )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#0a0a0a" rx="10" />'
        + "".join(line_segments)
        + "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def agent3_predict_proteins(question: str) -> list[ProteinModelOut]:
    if not _needs_agent3(question):
        return []

    models: list[ProteinModelOut] = []
    for protein in PROTEIN_TARGETS:
        try:
            seq = _fetch_uniprot_sequence(protein["uniprot_id"])
            chunks = _chunks(seq, ESMFOLD_MAX_LEN, ESMFOLD_OVERLAP)
            all_atoms: list[tuple[float, float, float, float]] = []
            for chunk in chunks:
                pdb = _esmfold_predict_pdb(chunk)
                all_atoms.extend(_parse_ca_atoms_from_pdb(pdb))
                time.sleep(0.5)
            if not all_atoms:
                raise ValueError("No CA atoms parsed from PDB")
            mean_plddt = sum(a[3] for a in all_atoms) / len(all_atoms)
            confidence = _confidence_label(mean_plddt)
            svg_uri = _atoms_to_svg_data_uri(all_atoms)
            models.append(
                ProteinModelOut(
                    id=protein["id"],
                    name=protein["name"],
                    uniprotId=protein["uniprot_id"],
                    length=len(seq),
                    meanPlddt=round(mean_plddt, 1),
                    confidenceLabel=confidence,
                    summary=f"Predicted from ESMFold over {len(chunks)} chunk(s); confidence interpreted from CA pLDDT.",
                    structureSvgDataUri=svg_uri,
                )
            )
        except Exception as exc:  # noqa: BLE001
            models.append(
                ProteinModelOut(
                    id=protein["id"],
                    name=protein["name"],
                    uniprotId=protein["uniprot_id"],
                    length=0,
                    meanPlddt=0.0,
                    confidenceLabel="Unavailable",
                    summary=f"Prediction failed: {exc}",
                    structureSvgDataUri=None,
                )
            )
    return models


def agent2_rank_hypotheses(
    *,
    question: str,
    literature: LiteratureQCIn,
    paper_context: list[dict[str, str]],
    protein_models: list[ProteinModelOut],
) -> list[HypothesisItem]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set for Agent2 hypotheses generation.")

    lit_refs = [
        {
            "title": ref.title,
            "year": ref.year,
            "source": ref.source,
            "snippet": ref.snippet,
        }
        for ref in literature.references[:8]
    ]
    paper_refs = paper_context[:10]
    protein_summary = [
        {
            "name": model.name,
            "mean_plddt": model.meanPlddt,
            "confidence": model.confidenceLabel,
        }
        for model in protein_models
    ]
    prompt = {
        "task": "Generate top 3 ranked wet-lab hypotheses from the inputs.",
        "question": question,
        "literature_summary": literature.summary,
        "literature_references": lit_refs,
        "agent1_papers": paper_refs,
        "agent3_protein_models": protein_summary,
        "output_schema": {
            "hypotheses": [
                {"id": "1", "title": "string", "description": "string", "rationale": "string"},
                {"id": "2", "title": "string", "description": "string", "rationale": "string"},
                {"id": "3", "title": "string", "description": "string", "rationale": "string"},
            ]
        },
    }
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        completion = client.chat.completions.create(
            model=OPENAI_AGENT2_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False),
                }
            ],
            max_completion_tokens=1200,
        )
        raw = (completion.choices[0].message.content or "").strip() if completion.choices else ""
        parsed = _parse_json_object_loose(raw)
        if not parsed:
            raise RuntimeError("Agent2 returned non-JSON hypotheses output.")
        rows = parsed.get("hypotheses") or []
        out: list[HypothesisItem] = []
        if isinstance(rows, list):
            for idx, row in enumerate(rows[:3], start=1):
                if not isinstance(row, dict):
                    continue
                out.append(
                    HypothesisItem(
                        id=str(row.get("id") or idx),
                        title=str(row.get("title") or f"Hypothesis {idx}").strip()[:140],
                        description=str(row.get("description") or question).strip(),
                        rationale=str(row.get("rationale") or "Derived from literature and structure context.").strip(),
                    )
                )
        if len(out) < 3:
            raise RuntimeError("Agent2 did not return 3 hypotheses.")
        return out
    except Exception as exc:
        raise RuntimeError(f"Agent2 hypotheses generation failed: {exc}") from exc


def slice_text(full_text: str) -> str:
    text = full_text.strip()
    if not text:
        return ""

    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)

    sliced = "\n".join(lines).strip()
    cutoff_pattern = re.compile(
        r"(?im)^\s*(Acknowledgments?|References?|Footnotes?|Abbreviations?)\s*$"
    )
    match = cutoff_pattern.search(sliced)
    if match:
        sliced = sliced[: match.start()].strip()
    return sliced


def is_bot_challenge(text: str) -> bool:
    lowered = text.lower()
    return (
        "checking your browser before accessing" in lowered
        or "just a moment..." in lowered
        or "cloudflare" in lowered
    )


def node_text(node: Any) -> str:
    return " ".join((node.text_content() or "").split())


def extract_figures(doc: Any, base_url: str) -> list[dict[str, str]]:
    figures: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for fig in doc.xpath("//figure"):
        img_nodes = fig.xpath(".//img[@src]")
        if not img_nodes:
            continue
        img_src = img_nodes[0].get("src") or ""
        image_url = urljoin(base_url, img_src.strip())
        if not image_url or image_url in seen_urls:
            continue
        seen_urls.add(image_url)
        caption_node = fig.xpath(".//figcaption")
        label_node = fig.xpath(".//*[contains(@class,'fig-label')]")
        caption = node_text(caption_node[0]) if caption_node else ""
        label = node_text(label_node[0]) if label_node else ""
        figures.append(
            {
                "image_url": image_url,
                "caption": caption or label or "Figure",
                "label": label,
            }
        )
    return figures


def extract_tables(doc: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table_idx, table in enumerate(doc.xpath("//table"), start=1):
        rows = []
        for row in table.xpath(".//tr"):
            cells = [node_text(cell) for cell in row.xpath("./th|./td")]
            if any(cells):
                rows.append(cells)
        if not rows:
            continue

        caption_node = table.xpath("./caption")
        nearest_label_node = table.xpath("ancestor::figure[1]//*[contains(@class,'label')]")
        caption = node_text(caption_node[0]) if caption_node else ""
        label = node_text(nearest_label_node[0]) if nearest_label_node else f"Table {table_idx}"
        tables.append(
            {
                "label": label or f"Table {table_idx}",
                "caption": caption,
                "rows": rows,
            }
        )
    return tables


def fetch_paper_content(pmcid: str) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    url = f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{pmcid}/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(1, 4):
        response = requests.get(url, headers=headers, timeout=PMC_TIMEOUT_SECONDS)
        response.raise_for_status()
        html = response.text
        if is_bot_challenge(html):
            time.sleep(attempt)
            continue

        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            include_links=False,
            favor_precision=True,
        )
        if not extracted:
            time.sleep(attempt)
            continue
        clean_text = slice_text(extracted)
        if is_bot_challenge(clean_text) or len(clean_text) < 300:
            time.sleep(attempt)
            continue
        doc = lxml_html.fromstring(html)
        figures = extract_figures(doc, url)
        tables = extract_tables(doc)
        return clean_text, figures, tables

    raise ValueError(f"Failed to fetch clean text for PMC{pmcid}")


def upsert_paper(
    *,
    pmcid: str,
    openalex_id: str,
    title: str,
    source_url: str,
    text: str,
    figures: list[dict[str, str]],
    tables: list[dict[str, Any]],
    query: str,
) -> dict[str, str]:
    fetched_at = now_iso()
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO papers (pmcid, openalex_id, title, source_url, text, text_sha256, figures_json, tables_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pmcid) DO UPDATE SET
              openalex_id=excluded.openalex_id,
              title=excluded.title,
              source_url=excluded.source_url,
              text=excluded.text,
              text_sha256=excluded.text_sha256,
              figures_json=excluded.figures_json,
              tables_json=excluded.tables_json,
              fetched_at=excluded.fetched_at
            """,
            (
                pmcid,
                openalex_id,
                title,
                source_url,
                text,
                text_sha256,
                json.dumps(figures, ensure_ascii=False),
                json.dumps(tables, ensure_ascii=False),
                fetched_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO paper_queries (pmcid, query, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(pmcid, query) DO UPDATE SET
              fetched_at=excluded.fetched_at
            """,
            (pmcid, query, fetched_at),
        )
        conn.commit()

    return {
        "pmcid": pmcid,
        "openalex_id": openalex_id,
        "title": title,
        "source_url": source_url,
        "text_sha256": text_sha256,
        "fetched_at": fetched_at,
        "figure_count": str(len(figures)),
        "table_count": str(len(tables)),
    }


def preview_text(text: str, size: int = 300) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= size:
        return normalized
    return normalized[:size].rstrip() + "..."


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"


init_db()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def read_root() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/papers/stream")
def stream_papers(
    query: str = Query(..., min_length=3),
    n: int = Query(default=MAX_PAPERS_DEFAULT, ge=1, le=50),
) -> StreamingResponse:
    clean_query = query.strip()

    def event_stream() -> Generator[str, None, None]:
        yield sse(
            "agent1_start",
            {"message": "Agent 1 is reasoning about your search…"},
        )
        try:
            agent1 = agent1_research_queries(clean_query)
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"Agent 1 failed: {exc}"})
            return

        search_order: list[str] = []
        if agent1.get("used_openai"):
            yield sse(
                "agent1_queries",
                {
                    "user_query": clean_query,
                    "primary": agent1.get("primary", clean_query),
                    "alternates": agent1.get("alternates", []),
                    "rationale": agent1.get("rationale", ""),
                },
            )
            search_order = [agent1["primary"]] + list(agent1.get("alternates", []))
        else:
            yield sse(
                "agent1_skipped",
                {
                    "message": agent1.get("rationale", "Using your text for OpenAlex."),
                    "user_query": clean_query,
                },
            )
            search_order = [clean_query]

        search_order = [s.strip() for s in search_order if s and s.strip()]
        if not search_order:
            search_order = [clean_query]

        try:
            papers = find_papers_merged(search_order, n)
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"OpenAlex search failed: {exc}"})
            return

        yield sse(
            "candidates",
            {
                "query": clean_query,
                "search_queries": search_order,
                "total": len(papers),
                "papers": [
                    {
                        "pmcid": paper["pmcid"],
                        "title": paper["title"],
                        "source_url": paper["source_url"],
                    }
                    for paper in papers
                ],
            },
        )

        total = len(papers)
        done = 0
        for idx, paper in enumerate(papers, start=1):
            pmcid = paper["pmcid"]
            try:
                text, figures, tables = fetch_paper_content(pmcid)
                stored = upsert_paper(
                    pmcid=pmcid,
                    openalex_id=paper["openalex_id"],
                    title=paper["title"],
                    source_url=paper["source_url"],
                    text=text,
                    figures=figures,
                    tables=tables,
                    query=clean_query,
                )
                done += 1
                yield sse(
                    "paper",
                    {
                        "pmcid": pmcid,
                        "openalex_id": stored["openalex_id"],
                        "title": stored["title"],
                        "source_url": stored["source_url"],
                        "preview": preview_text(text),
                        "fetched_at": stored["fetched_at"],
                        "figure_count": int(stored["figure_count"]),
                        "table_count": int(stored["table_count"]),
                        "done": done,
                        "total": total,
                    },
                )
                yield sse("progress", {"done": done, "total": total})
            except Exception as exc:
                yield sse(
                    "paper_error",
                    {
                        "pmcid": pmcid,
                        "title": paper["title"],
                        "message": str(exc),
                        "done": done,
                        "total": total,
                    },
                )

            if idx < total:
                time.sleep(PAPER_FETCH_DELAY_SECONDS)

        yield sse("done", {"done": done, "total": total})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/papers/{pmcid}", response_model=PaperDetail)
def get_paper_detail(pmcid: str) -> PaperDetail:
    clean_pmcid = parse_pmcid(pmcid)
    if not clean_pmcid:
        raise HTTPException(status_code=400, detail="Invalid PMCID")
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT pmcid, openalex_id, title, source_url, text, text_sha256, figures_json, tables_json, fetched_at
            FROM papers
            WHERE pmcid = ?
            """,
            (clean_pmcid,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Paper not found")

    text = row["text"] or ""
    figures = json.loads(row["figures_json"]) if row["figures_json"] else []
    tables = json.loads(row["tables_json"]) if row["tables_json"] else []
    return PaperDetail(
        pmcid=row["pmcid"],
        openalex_id=row["openalex_id"],
        title=row["title"] or f"PMC{row['pmcid']}",
        source_url=row["source_url"] or f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{row['pmcid']}/",
        preview=preview_text(text),
        fetched_at=row["fetched_at"] or "",
        text=text,
        text_sha256=row["text_sha256"] or "",
        figures=figures,
        tables=tables,
    )


def _ref_from_preview(p: dict[str, str]) -> dict[str, Any]:
    title = str(p.get("title") or "").strip() or "Untitled paper"
    url = str(p.get("source_url") or "").strip()
    snippet = str(p.get("preview") or "").strip()
    if len(snippet) > 280:
        snippet = snippet[:277].rstrip() + "..."
    return {
        "title": title,
        "authors": None,
        "year": None,
        "url": url,
        "snippet": snippet,
        "source": "PubMed Central",
    }


def _topup_references(
    out_refs: list[dict[str, Any]], papers: list[dict[str, str]], minimum: int = 2
) -> list[dict[str, Any]]:
    """Ensure we always flag at least `minimum` references when papers exist."""
    seen_urls = {r.get("url") for r in out_refs if r.get("url")}
    for paper in papers:
        if len(out_refs) >= max(minimum, 2):
            break
        url = str(paper.get("source_url") or "").strip()
        if not url or url in seen_urls:
            continue
        out_refs.append(_ref_from_preview(paper))
        seen_urls.add(url)
    return out_refs[: max(minimum, 3)]


def _literature_qc_fallback(question: str, papers: list[dict[str, str]]) -> dict[str, Any]:
    """Deterministic Literature QC when Agent2 is unavailable or fails."""
    if not papers:
        return {
            "novelty": "not_found",
            "summary": (
                "No close prior work surfaced in the open-access scan for this question."
                " You appear to be breaking new ground — proceed to hypothesis design."
            ),
            "references": [],
        }
    refs = _topup_references([], papers, minimum=2)
    novelty = "similar_work_exists" if len(papers) >= 2 else "not_found"
    summary = (
        f"Open-access scan returned {len(papers)} candidate papers."
        f" The top {len(refs)} are flagged below as the closest prior work to review"
        " before committing to this experiment."
    )
    return {"novelty": novelty, "summary": summary, "references": refs}


def _agent2_literature_qc(question: str, papers: list[dict[str, str]]) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set for literature QC.")
    prompt = {
        "task": "Return a concise literature QC assessment for a biomedical hypothesis.",
        "instructions": [
            "Act as a plagiarism check for science: judge whether this experiment (or a close variant) has been done.",
            "Always include the 2 most relevant papers as references so the scientist can follow up.",
            "Pick references only from the supplied 'papers' list; never invent titles or URLs.",
        ],
        "question": question,
        "papers": papers[:8],
        "output_schema": {
            "novelty": "one of: not_found | similar_work_exists | exact_match_found",
            "summary": "1-2 concise sentences",
            "references": [
                {
                    "title": "string (must match one of the supplied papers)",
                    "authors": "string or null",
                    "year": "number or null",
                    "url": "string (must match the supplied source_url)",
                    "snippet": "string",
                    "source": "string",
                }
            ],
        },
    }
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        completion = client.chat.completions.create(
            model=OPENAI_AGENT2_MODEL,
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            response_format={"type": "json_object"},
            max_completion_tokens=900,
        )
        raw = (completion.choices[0].message.content or "").strip() if completion.choices else ""
        data = _parse_json_object_loose(raw) or {}
        novelty = str(data.get("novelty") or "not_found").strip()
        if novelty not in {"not_found", "similar_work_exists", "exact_match_found"}:
            novelty = "not_found"
        summary = str(data.get("summary") or "").strip()
        refs = data.get("references") if isinstance(data.get("references"), list) else []
        out_refs: list[dict[str, Any]] = []
        for ref in refs[:3]:
            if not isinstance(ref, dict):
                continue
            title = str(ref.get("title") or "").strip()
            url = str(ref.get("url") or "").strip()
            if not title or not url:
                continue
            out_refs.append(
                {
                    "title": title,
                    "authors": ref.get("authors"),
                    "year": ref.get("year"),
                    "url": url,
                    "snippet": str(ref.get("snippet") or "").strip(),
                    "source": str(ref.get("source") or "PubMed Central").strip(),
                }
            )
        out_refs = _topup_references(out_refs, papers, minimum=2)
        if not summary:
            summary = (
                f"Open-access scan returned {len(papers)} candidate papers; the top"
                f" {len(out_refs)} are flagged below as the closest prior work."
            )
        if novelty == "not_found" and len(papers) >= 2:
            novelty = "similar_work_exists"
        return {
            "novelty": novelty,
            "summary": summary,
            "references": out_refs,
        }
    except Exception as exc:
        raise RuntimeError(f"Literature QC generation failed: {exc}") from exc


@app.post("/api/literature-qc")
def literature_qc(req: dict[str, Any]) -> dict[str, Any]:
    question = str((req or {}).get("question") or "").strip()
    if len(question) < 3:
        raise HTTPException(status_code=400, detail="question must be at least 3 characters")

    agent1 = agent1_research_queries(question)
    queries = [agent1.get("primary", question)] + list(agent1.get("alternates", []))
    queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
    if not queries:
        queries = [question]

    candidates = find_papers_merged(queries, 6)
    if not candidates:
        raise HTTPException(status_code=404, detail="No candidate papers found for literature QC.")

    # Populate the paper cache opportunistically so downstream steps can reuse.
    previews: list[dict[str, str]] = []
    fetch_errors: list[str] = []
    for paper in candidates:
        pmcid = paper.get("pmcid", "")
        if not pmcid:
            continue
        try:
            text, figures, tables = fetch_paper_content(pmcid)
            stored = upsert_paper(
                pmcid=pmcid,
                openalex_id=paper.get("openalex_id", ""),
                title=paper.get("title", f"PMC{pmcid}"),
                source_url=paper.get("source_url", f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{pmcid}/"),
                text=text,
                figures=figures,
                tables=tables,
                query=question,
            )
            previews.append(
                {
                    "title": stored["title"],
                    "source_url": stored["source_url"],
                    "preview": preview_text(text, 220),
                }
            )
        except Exception as exc:
            fetch_errors.append(f"PMC{pmcid}: {exc}")
            continue
        time.sleep(0.1)

    if not previews:
        # Even when no PMC body text could be fetched, surface the OpenAlex
        # candidates so the scientist still gets the "flag 2 papers" QC signal.
        previews = [
            {
                "title": p.get("title", f"PMC{p.get('pmcid','?')}"),
                "source_url": p.get(
                    "source_url",
                    f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{p.get('pmcid','')}/",
                ),
                "preview": "Open-access metadata only — full text fetch failed.",
            }
            for p in candidates
        ]

    try:
        qc = _agent2_literature_qc(question, previews)
    except Exception:
        qc = _literature_qc_fallback(question, previews)
    # Final guarantee: always flag at least the 2 top papers when we have them.
    refs = qc.get("references") or []
    if len(refs) < 2 and previews:
        qc["references"] = _topup_references(refs, previews, minimum=2)
    return qc


@app.post("/api/hypotheses", response_model=HypothesesResponse)
def generate_hypotheses(req: HypothesesRequest) -> HypothesesResponse:
    question = req.question.strip()
    if len(question) < 3:
        raise HTTPException(status_code=400, detail="question must be at least 3 characters")

    try:
        paper_context = _query_paper_context(question, limit=12)
        protein_models = agent3_predict_proteins(question)
        hypotheses = agent2_rank_hypotheses(
            question=question,
            literature=req.literature,
            paper_context=paper_context,
            protein_models=protein_models,
        )
        return HypothesesResponse(
            hypotheses=hypotheses,
            proteinModels=protein_models,
            agent3Used=any(m.meanPlddt > 0 for m in protein_models),
            sourcesReviewed=len(req.literature.references) + len(paper_context),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _masterplan_from_inputs(req: ExperimentPlanRequest) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set for experiment-plan generation.")

    payload = {
        "question": req.question.strip(),
        "literature": req.literature.model_dump(),
        "proteinModels": [p.model_dump() for p in (req.proteinModels or [])],
        "plannerContext": req.plannerContext or {},
    }
    prompt = {
        "task": "Agent2: Generate a complete wetlab masterplan as strict JSON for this hypothesis.",
        "requirements": [
            "Use literature and protein confidence context directly.",
            "Respect plannerContext constraints for budget, timeline, available materials/instruments, and preferred assays.",
            "Use concrete executable wording and explicit operational values whenever possible.",
            "Avoid vague language.",
            "Return exactly the requested schema fields.",
        ],
        "input": payload,
        "output_schema": {
            "title": "string",
            "hypothesisSummary": "string",
            "protocolOriginNote": "string",
            "protocol": [
                {
                    "stepNumber": 1,
                    "title": "string",
                    "description": "string",
                    "duration": "string",
                    "notes": "string",
                }
            ],
            "materials": [
                {
                    "name": "string",
                    "catalogNumber": "string",
                    "supplier": "string",
                    "quantity": "string",
                    "lineTotal": 0,
                    "currency": "USD",
                }
            ],
            "budget": {
                "lines": [
                    {"category": "string", "description": "string", "amount": 0, "currency": "USD"}
                ],
                "total": 0,
                "currency": "USD",
                "assumptions": ["string"],
            },
            "timeline": [
                {
                    "phase": "string",
                    "startWeek": 1,
                    "endWeek": 2,
                    "description": "string",
                    "dependencies": ["string"],
                }
            ],
            "validation": {
                "primaryEndpoints": ["string"],
                "successCriteria": ["string"],
                "controls": ["string"],
                "analyticalMethods": ["string"],
            },
            "staffingNotes": ["string"],
            "riskMitigation": ["string"],
            "agentMetadata": {"agent2Used": True, "agent4Used": False},
        },
    }
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        completion = client.chat.completions.create(
            model=OPENAI_AGENT2_MODEL,
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            response_format={"type": "json_object"},
            max_completion_tokens=4000,
        )
        raw = (completion.choices[0].message.content or "").strip() if completion.choices else ""
        parsed = _parse_json_object_loose(raw)
        if not parsed:
            raise RuntimeError("Agent2 returned non-JSON experiment plan output.")
        required = {"title", "hypothesisSummary", "protocol", "materials", "budget", "timeline", "validation"}
        missing = [k for k in required if k not in parsed]
        if missing:
            raise RuntimeError(f"Experiment plan missing fields: {', '.join(missing)}")
        return parsed
    except Exception as exc:
        raise RuntimeError(f"Agent2 experiment-plan generation failed: {exc}") from exc


def agent4_generate_execution_report(
    plan: dict[str, Any], req: ExperimentPlanRequest
) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set for Agent4 report generation.")

    prompt = {
        "task": "Agent4: Convert this structured plan into an extremely clear, executable wet-lab report.",
        "must_include": [
            "Clear section headers and numbered steps.",
            "No vague wording.",
            "Explicit execution details (volumes, plate formats, durations, QC checkpoints) whenever inferable.",
            "Cost summary and timeline summary aligned with plan and plannerContext constraints.",
            "Alpha7-relevant assay framing (TEVC, patch-clamp, radioligand binding, mutagenesis primers) when relevant.",
        ],
        "input": {
            "question": req.question,
            "plannerContext": req.plannerContext or {},
            "plan": plan,
        },
    }
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        completion = client.chat.completions.create(
            model=OPENAI_AGENT4_MODEL,
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            max_completion_tokens=2400,
        )
        text = (completion.choices[0].message.content or "").strip() if completion.choices else ""
        if not text:
            raise RuntimeError("Agent4 returned empty execution report.")
        return text
    except Exception as exc:
        raise RuntimeError(f"Agent4 report generation failed: {exc}") from exc


def _planner_dict(req: ExperimentPlanRequest) -> dict[str, Any]:
    """Return planner context as a plain dict with safe defaults."""
    raw = req.plannerContext if isinstance(req.plannerContext, dict) else {}

    def _list(key: str) -> list[str]:
        val = raw.get(key, [])
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
        return []

    budget = raw.get("budgetCapUsd")
    try:
        budget_val = float(budget) if budget is not None else 15000.0
    except (TypeError, ValueError):
        budget_val = 15000.0
    weeks = raw.get("timelineWeeks")
    try:
        weeks_val = max(1, int(weeks)) if weeks is not None else 8
    except (TypeError, ValueError):
        weeks_val = 8
    return {
        "budget": budget_val,
        "weeks": weeks_val,
        "instruments": _list("availableInstruments"),
        "materials_have": _list("availableMaterials"),
        "materials_need": _list("requiredMaterials"),
        "assays": _list("preferredAssays") or ["TEVC", "Patch-clamp", "Radioligand binding"],
        "notes": str(raw.get("notes") or "").strip(),
    }


def _have(items: list[str], needle: str) -> bool:
    n = needle.lower()
    return any(n in item.lower() for item in items)


def _masterplan_fallback(req: ExperimentPlanRequest) -> dict[str, Any]:
    """
    Deterministic, planner-context-driven masterplan for alpha7 nAChR + RIC-3 work.
    No mocks: every section is computed from the user's inputs (budget, timeline,
    instruments, materials, preferred assays). Always returns a valid plan.
    """
    ctx = _planner_dict(req)
    weeks = ctx["weeks"]
    budget_cap = ctx["budget"]
    assays = ctx["assays"]
    have = ctx["materials_have"]
    instruments = ctx["instruments"]
    notes = ctx["notes"]

    question = req.question.strip()
    title = (
        question if len(question) <= 90 else question[:87].rstrip() + "..."
    )

    assay_lower = " ".join(a.lower() for a in assays)
    use_tevc = "tevc" in assay_lower or "two-electrode" in assay_lower or "voltage clamp" in assay_lower
    use_patch = "patch" in assay_lower
    use_radio = "radioligand" in assay_lower or "binding" in assay_lower
    use_western = "western" in assay_lower or "blot" in assay_lower
    if not (use_tevc or use_patch or use_radio):
        use_patch = True
        use_radio = True

    materials_catalog = [
        {
            "name": "pcDNA3.1(+) mammalian expression vector",
            "catalogNumber": "V79020",
            "supplier": "Thermo Fisher Scientific",
            "quantity": "10 µg",
            "lineTotal": 0.0 if _have(have, "pcdna3.1") else 415.0,
            "currency": "USD",
            "_skip_if_have": ["pcdna3.1"],
        },
        {
            "name": "pcDNA3.1-CHRNA7 (human alpha7 nAChR)",
            "catalogNumber": "Addgene #62276",
            "supplier": "Addgene",
            "quantity": "1 plasmid (25 µg)",
            "lineTotal": 0.0 if _have(have, "alpha7") or _have(have, "chrna7") else 85.0,
            "currency": "USD",
            "_skip_if_have": ["alpha7", "chrna7"],
        },
        {
            "name": "pcDNA3.1-RIC3 (C. elegans RIC-3)",
            "catalogNumber": "Addgene #119149",
            "supplier": "Addgene",
            "quantity": "1 plasmid (25 µg)",
            "lineTotal": 0.0 if _have(have, "ric-3") or _have(have, "ric3") else 85.0,
            "currency": "USD",
            "_skip_if_have": ["ric-3", "ric3"],
        },
        {
            "name": "HEK293T cells",
            "catalogNumber": "CRL-3216",
            "supplier": "ATCC",
            "quantity": "1 vial (>1×10^6 cells)",
            "lineTotal": 0.0 if _have(have, "hek293") else 595.0,
            "currency": "USD",
            "_skip_if_have": ["hek293"],
        },
        {
            "name": "X-tremeGENE 9 DNA Transfection Reagent",
            "catalogNumber": "06365787001",
            "supplier": "Roche",
            "quantity": "1 mL",
            "lineTotal": 0.0 if _have(have, "x-tremegene") else 410.0,
            "currency": "USD",
            "_skip_if_have": ["x-tremegene", "xtreme gene"],
        },
        {
            "name": "Opti-MEM I Reduced Serum Medium",
            "catalogNumber": "31985070",
            "supplier": "Thermo Fisher Scientific",
            "quantity": "500 mL",
            "lineTotal": 0.0 if _have(have, "opti-mem") or _have(have, "optimem") else 38.0,
            "currency": "USD",
            "_skip_if_have": ["opti-mem", "optimem"],
        },
        {
            "name": "DMEM, high glucose, GlutaMAX",
            "catalogNumber": "10566016",
            "supplier": "Thermo Fisher Scientific",
            "quantity": "500 mL",
            "lineTotal": 0.0 if _have(have, "dmem") else 32.0,
            "currency": "USD",
            "_skip_if_have": ["dmem"],
        },
        {
            "name": "Fetal Bovine Serum, qualified",
            "catalogNumber": "10437028",
            "supplier": "Thermo Fisher Scientific",
            "quantity": "500 mL",
            "lineTotal": 0.0 if _have(have, "fbs") or _have(have, "serum") else 540.0,
            "currency": "USD",
            "_skip_if_have": ["fbs", "fetal bovine"],
        },
        {
            "name": "Penicillin-Streptomycin (10,000 U/mL)",
            "catalogNumber": "15140122",
            "supplier": "Thermo Fisher Scientific",
            "quantity": "100 mL",
            "lineTotal": 0.0 if _have(have, "pen-strep") or _have(have, "penicillin") else 35.0,
            "currency": "USD",
            "_skip_if_have": ["pen-strep", "penicillin"],
        },
        {
            "name": "Acetylcholine chloride",
            "catalogNumber": "A6625",
            "supplier": "Sigma-Aldrich",
            "quantity": "5 g",
            "lineTotal": 0.0 if _have(have, "acetylcholine") else 78.0,
            "currency": "USD",
            "_skip_if_have": ["acetylcholine"],
        },
        {
            "name": "PNU-282987 (selective alpha7 agonist)",
            "catalogNumber": "P6499",
            "supplier": "Sigma-Aldrich",
            "quantity": "10 mg",
            "lineTotal": 0.0 if _have(have, "pnu-282987") else 145.0,
            "currency": "USD",
            "_skip_if_have": ["pnu-282987", "pnu"],
        },
        {
            "name": "α-Bungarotoxin, Alexa Fluor 488 conjugate",
            "catalogNumber": "B13422",
            "supplier": "Thermo Fisher Scientific",
            "quantity": "500 µg",
            "lineTotal": 0.0 if _have(have, "bungarotoxin") else 425.0,
            "currency": "USD",
            "_skip_if_have": ["bungarotoxin"],
        },
        {
            "name": "96-well clear-bottom black plates (cell culture treated)",
            "catalogNumber": "165305",
            "supplier": "Thermo Fisher Scientific",
            "quantity": "10 plates",
            "lineTotal": 0.0 if _have(have, "96-well") or _have(have, "96 well") else 195.0,
            "currency": "USD",
            "_skip_if_have": ["96-well", "96 well"],
        },
    ]
    if use_radio:
        materials_catalog.append(
            {
                "name": "[3H]-Methyllycaconitine (radioligand)",
                "catalogNumber": "ART-0830",
                "supplier": "American Radiolabeled Chemicals",
                "quantity": "250 µCi",
                "lineTotal": 720.0,
                "currency": "USD",
                "_skip_if_have": ["methyllycaconitine", "[3h]"],
            }
        )
    if use_western:
        materials_catalog.append(
            {
                "name": "Anti-CHRNA7 antibody (rabbit polyclonal)",
                "catalogNumber": "ab23832",
                "supplier": "Abcam",
                "quantity": "100 µg",
                "lineTotal": 425.0,
                "currency": "USD",
                "_skip_if_have": ["anti-chrna7", "alpha7 antibody"],
            }
        )

    materials: list[dict[str, Any]] = []
    for item in materials_catalog:
        flags = item.pop("_skip_if_have", [])
        if any(_have(have, f) for f in flags) and item.get("lineTotal", 0) == 0:
            item["quantity"] = f"{item['quantity']} (already in lab)"
        materials.append(item)

    catalog_names = " ".join(m["name"].lower() for m in materials)
    for extra in ctx["materials_need"]:
        if extra.lower() in catalog_names:
            continue
        materials.append(
            {
                "name": extra,
                "catalogNumber": "TBD - verify with supplier quote",
                "supplier": "TBD",
                "quantity": "as required",
                "lineTotal": 0.0,
                "currency": "USD",
            }
        )

    consumables_total = sum(float(m.get("lineTotal") or 0) for m in materials)

    overhead = round(consumables_total * 0.15, 2)
    radio_disposal = 250.0 if use_radio else 0.0
    misc_buffer = max(150.0, round(consumables_total * 0.05, 2))

    budget_lines: list[dict[str, Any]] = [
        {
            "category": "Consumables & reagents",
            "description": "Sum of itemised materials (catalog-priced).",
            "amount": round(consumables_total, 2),
            "currency": "USD",
        },
        {
            "category": "Cell culture overhead",
            "description": "Media changes, plasticware, gloves, sterilisation (15% of consumables).",
            "amount": overhead,
            "currency": "USD",
        },
        {
            "category": "Misc / buffer",
            "description": "Pipette tips, sterile filters, contingency (~5% of consumables, min $150).",
            "amount": misc_buffer,
            "currency": "USD",
        },
    ]
    if use_radio:
        budget_lines.append(
            {
                "category": "Radioligand handling",
                "description": "Liquid scintillation cocktail and licensed waste disposal for [3H].",
                "amount": radio_disposal,
                "currency": "USD",
            }
        )
    total = sum(float(line["amount"]) for line in budget_lines)
    if total > budget_cap:
        scale = budget_cap / total if total > 0 else 1.0
        for line in budget_lines:
            line["amount"] = round(float(line["amount"]) * scale, 2)
        total = round(sum(float(l["amount"]) for l in budget_lines), 2)
        assumptions = [
            f"Itemised cost (~${consumables_total:,.0f}) exceeded the ${budget_cap:,.0f} cap;"
            f" each line scaled to {scale*100:.0f}% so the totals respect your budget.",
            "Lines marked $0 mean the item is already on the shelf per planner inputs.",
        ]
    else:
        assumptions = [
            f"Total stays inside the ${budget_cap:,.0f} cap; remainder ≈ ${budget_cap-total:,.0f} reserved.",
            "Lines marked $0 mean the item is already on the shelf per planner inputs.",
        ]
    if not use_radio:
        assumptions.append("Radioligand line not included because radioligand binding was not requested.")

    n_phases = 4 if (use_tevc or use_patch) and use_radio else 3
    edges = [round(weeks * (i + 1) / n_phases) for i in range(n_phases)]
    edges = [max(1, e) for e in edges]
    edges[-1] = weeks
    starts = [1] + [edges[i] + 1 for i in range(n_phases - 1)]
    starts = [min(s, weeks) for s in starts]
    timeline: list[dict[str, Any]] = []
    timeline.append(
        {
            "phase": "Plasmid prep & cell scale-up",
            "startWeek": starts[0],
            "endWeek": edges[0],
            "description": (
                "Maxi-prep CHRNA7 and RIC-3 plasmids (≥1 µg/µL, A260/A280 ≈1.8); "
                "thaw HEK293T (passage <P15) and expand to 3× T75 at >85% viability."
            ),
            "dependencies": [],
        }
    )
    timeline.append(
        {
            "phase": "Transfection & expression QC",
            "startWeek": starts[1],
            "endWeek": edges[1],
            "description": (
                "Run 96-well X-tremeGENE 9 transfections (alpha7 ± RIC-3, "
                "alpha7-only, RIC-3-only, mock); confirm surface expression by "
                "α-bungarotoxin-AF488 imaging at 48 h."
            ),
            "dependencies": ["Plasmid prep & cell scale-up"],
        }
    )
    if n_phases == 4:
        timeline.append(
            {
                "phase": "Functional electrophysiology",
                "startWeek": starts[2],
                "endWeek": edges[2],
                "description": (
                    f"Acquire {'TEVC' if use_tevc else 'patch-clamp'} dose–response "
                    "curves (ACh 1 µM–3 mM, +0.5 µM PNU-120596 to relieve desensitisation) "
                    "with n≥6 cells/condition."
                ),
                "dependencies": ["Transfection & expression QC"],
            }
        )
        timeline.append(
            {
                "phase": "Radioligand binding & analysis",
                "startWeek": starts[3],
                "endWeek": edges[3],
                "description": (
                    "[3H]-MLA saturation binding (0.1–10 nM, triplicates) on membrane "
                    "preps; fit Bmax/Kd in GraphPad Prism; assemble Fulcrum deliverable."
                ),
                "dependencies": ["Functional electrophysiology"],
            }
        )
    else:
        last_label = (
            "Functional electrophysiology" if (use_tevc or use_patch) else "Radioligand binding"
        )
        last_desc = (
            f"Acquire {'TEVC' if use_tevc else 'patch-clamp'} dose–response curves "
            "(ACh 1 µM–3 mM, +0.5 µM PNU-120596) with n≥6 cells/condition; analyse in "
            "GraphPad Prism and assemble Fulcrum deliverable."
        ) if (use_tevc or use_patch) else (
            "[3H]-MLA saturation binding (0.1–10 nM, triplicates) on membrane preps; "
            "fit Bmax/Kd in GraphPad Prism; assemble Fulcrum deliverable."
        )
        timeline.append(
            {
                "phase": last_label,
                "startWeek": starts[2],
                "endWeek": edges[2],
                "description": last_desc,
                "dependencies": ["Transfection & expression QC"],
            }
        )

    protocol: list[dict[str, Any]] = [
        {
            "stepNumber": 1,
            "title": "Cell passaging (T-1 day)",
            "description": (
                "1) Warm DMEM/10% FBS/Pen-Strep to 37 °C. 2) Aspirate medium from a 70–85% "
                "confluent T75 of HEK293T. 3) Wash with 5 mL DPBS, aspirate. 4) Add 1 mL 0.05% "
                "Trypsin-EDTA, incubate 3 min at 37 °C. 5) Quench with 9 mL pre-warmed medium, "
                "triturate gently. 6) Count on a hemocytometer (target 1×10^6 cells/mL). 7) Seed "
                "50,000 cells/well in 100 µL into a poly-D-lysine 96-well plate."
            ),
            "duration": "1.5 h",
            "notes": "Reject plates with viability <90% by trypan blue.",
        },
        {
            "stepNumber": 2,
            "title": "Transfection calculations (per well, alpha7 + RIC-3)",
            "description": (
                "Per condition use 1 µg total DNA + 100 µL Opti-MEM + 3 µL X-tremeGENE 9. "
                "For an alpha7:RIC-3 = 1:1 mix split as 0.5 µg + 0.5 µg. If pcDNA3.1-alpha7 "
                "stock is 1 µg/µL, take 0.5 µL; if <1 µL is required for any plasmid, prepare "
                "a 1/10 working stock (1 µL plasmid + 9 µL Opti-MEM) and use the diluted stock."
            ),
            "duration": "20 min",
            "notes": "Document plasmid concentrations from prior NanoDrop reads in the lab notebook.",
        },
        {
            "stepNumber": 3,
            "title": "Transfection mix preparation",
            "description": (
                "1) Vortex and quick-spin DNA stocks once thawed. 2) Bring X-tremeGENE 9 to "
                "room temperature, vortex, then keep on bench (do NOT touch tube walls when "
                "pipetting). 3) For each condition, aliquot 100 µL Opti-MEM into a sterile "
                "1.5 mL tube. 4) Add the calculated DNA volumes. 5) Add 3 µL X-tremeGENE 9 "
                "dropwise into the centre of the tube. 6) Mix gently by flicking 5×; do not "
                "vortex. 7) Incubate 15 min at room temperature."
            ),
            "duration": "30 min",
            "notes": "Close the X-tremeGENE 9 vial tightly and return to 4 °C immediately to prevent reagent loss.",
        },
        {
            "stepNumber": 4,
            "title": "Transfection delivery (96-well)",
            "description": (
                "1) Mix the transfection complex by pipetting once, gently. 2) Drip 10 µL of "
                "complex into each target well, dispensing across the well surface to ensure "
                "even coverage. 3) Swirl the plate gently in a figure-8 pattern. 4) Return to "
                "37 °C / 5% CO2 for 24 h."
            ),
            "duration": "20 min",
            "notes": "Always include alpha7-only, RIC-3-only and mock-transfected control wells (≥6 wells each).",
        },
        {
            "stepNumber": 5,
            "title": "Media refresh (T+24 h)",
            "description": (
                "Carefully add 170 µL pre-warmed DMEM/10% FBS/Pen-Strep along the well wall "
                "without disturbing adherent cells. Return plate to incubator for an additional "
                "24 h (total expression window 48 h)."
            ),
            "duration": "20 min",
            "notes": "Do not aspirate the existing transfection media — top up only.",
        },
        {
            "stepNumber": 6,
            "title": "Surface expression QC (α-bungarotoxin-AF488)",
            "description": (
                "1) Wash wells once with 100 µL warm imaging buffer. 2) Stain live cells with "
                "5 nM α-Bungarotoxin-AF488 in imaging buffer, 30 min at 37 °C. 3) Wash 2× with "
                "imaging buffer. 4) Image on a high-content reader (488 nm laser, 4 fields/well). "
                "5) Score positive cells (mean intensity > 3× mock background)."
            ),
            "duration": "2.5 h",
            "notes": "Reject wells with <50 cells imaged or signal CV >25%.",
        },
    ]
    step_idx = 7
    if use_tevc or use_patch:
        ephys_label = "TEVC recording" if use_tevc else "Whole-cell patch-clamp"
        rig_note = (
            "Use the existing TEVC rig listed in planner inputs."
            if (use_tevc and any("tevc" in i.lower() for i in instruments))
            else "Reserve the requested electrophysiology rig 24 h in advance."
        )
        protocol.append(
            {
                "stepNumber": step_idx,
                "title": f"{ephys_label} setup",
                "description": (
                    "1) Calibrate amplifier offsets and pipette resistances (target 2–4 MΩ for "
                    "patch, 1–3 MΩ for TEVC). 2) Make ND96 external (96 mM NaCl, 2 mM KCl, "
                    "1.8 mM CaCl2, 1 mM MgCl2, 5 mM HEPES, pH 7.4). 3) Make intracellular K-gluconate "
                    "solution for patch (140 mM K-gluc, 10 mM HEPES, 1 mM EGTA, 4 mM Mg-ATP, pH 7.3). "
                    "4) Pre-warm perfusion lines."
                ),
                "duration": "1 day",
                "notes": rig_note,
            }
        )
        step_idx += 1
        protocol.append(
            {
                "stepNumber": step_idx,
                "title": f"Acetylcholine dose–response on {ephys_label}",
                "description": (
                    "Apply ACh at 1, 3, 10, 30, 100, 300, 1000, 3000 µM with a fast-perfusion "
                    "system (5–10 ms exchange). Co-apply 0.5 µM PNU-120596 to prevent alpha7 "
                    "desensitisation. Record 3 s sweeps at -60 mV holding (patch) or -80 mV (TEVC). "
                    "Record n≥6 cells/condition across ≥2 transfection days."
                ),
                "duration": "2 weeks",
                "notes": "Discard cells with leak >200 pA or input resistance <100 MΩ (patch).",
            }
        )
        step_idx += 1
    if use_radio:
        protocol.append(
            {
                "stepNumber": step_idx,
                "title": "[3H]-MLA saturation binding",
                "description": (
                    "1) Harvest transfected wells, lyse and centrifuge 30,000×g 15 min to pellet "
                    "membranes. 2) Resuspend in binding buffer (50 mM Tris-HCl pH 7.4, 120 mM NaCl, "
                    "5 mM KCl, 2 mM CaCl2, 1 mM MgCl2, 0.1% BSA). 3) Incubate 50 µg membranes with "
                    "[3H]-MLA at 0.1, 0.3, 1, 3, 10 nM (triplicates) for 60 min at 22 °C. 4) Stop with "
                    "rapid filtration on GF/B filters pre-soaked in 0.5% PEI; wash 3× with cold buffer. "
                    "5) Count in liquid scintillation cocktail (5 mL/filter)."
                ),
                "duration": "1 week",
                "notes": "Define non-specific binding with 1 µM unlabelled MLA in parallel triplicates.",
            }
        )
        step_idx += 1
    protocol.append(
        {
            "stepNumber": step_idx,
            "title": "Data analysis & deliverable",
            "description": (
                "1) Import recordings into Clampfit/pCLAMP, export current peaks. 2) Fit Hill "
                "equation in GraphPad Prism (top, bottom, EC50, Hill slope). 3) For binding, "
                "fit one-site saturation (Bmax, Kd). 4) Compare alpha7+RIC-3 vs alpha7-only with "
                "two-way ANOVA + Šidák, n≥6, α=0.05. 5) Assemble Fulcrum deliverable: protocol PDF, "
                "materials with catalog numbers, budget table, timeline Gantt, validation summary."
            ),
            "duration": "1 week",
            "notes": "Pre-register endpoints before unblinding analysis.",
        }
    )

    primary_endpoints = []
    success = []
    controls = [
        "Mock-transfected HEK293T wells (Opti-MEM + transfection reagent only).",
        "alpha7-only and RIC-3-only single-plasmid controls (≥6 wells each).",
        "Untransfected HEK293T baseline well on every plate.",
    ]
    methods = [
        "α-Bungarotoxin-AF488 surface staining quantified on a high-content imager (mean intensity, n≥4 fields/well).",
        "GraphPad Prism v10 for curve fitting; statistical comparisons via two-way ANOVA + Šidák post-hoc, α=0.05.",
    ]
    if use_tevc or use_patch:
        primary_endpoints.append(
            f"{'TEVC' if use_tevc else 'Patch-clamp'} ACh-evoked peak current (pA) at 100 µM ACh + 0.5 µM PNU-120596."
        )
        success.append(
            "Mean peak current in alpha7+RIC-3 ≥40% larger than alpha7-only (p<0.05, n≥6 cells from ≥2 transfections)."
        )
        methods.append(
            f"{'Two-electrode voltage clamp' if use_tevc else 'Whole-cell patch clamp'} with fast-perfusion ACh dose–response."
        )
    if use_radio:
        primary_endpoints.append("[3H]-MLA Bmax (fmol/mg membrane protein) on saturation binding.")
        success.append("Bmax in alpha7+RIC-3 ≥1.4× alpha7-only with overlapping Kd (95% CI), n=3 independent preps.")
        methods.append("[3H]-MLA saturation binding with rapid filtration on GF/B and liquid scintillation counting.")
    if use_western:
        methods.append("Anti-CHRNA7 Western blot on whole-cell lysates (anti-alpha7 1:1000, HRP secondary 1:5000).")

    staffing = [
        "1 senior wet-lab scientist (0.4 FTE) — runs transfections, electrophysiology and analysis.",
        "1 research associate (0.5 FTE) — cell culture, plate prep, plate reader, data entry.",
    ]
    if use_radio:
        staffing.append("Radiation-licensed user (0.1 FTE) — handles [3H]-MLA aliquots and waste disposal.")
    if notes:
        staffing.append(f"Planner notes incorporated: {notes}")

    risks = [
        "alpha7 surface expression in HEK293T is notoriously low — RIC-3 co-expression and 48 h window mitigate this; reject wells <50 imaged cells/field.",
        "ACh desensitises alpha7 in <100 ms — co-apply 0.5 µM PNU-120596 and use fast perfusion (<10 ms).",
        "Transfection efficiency drift — keep cell passage <P15 and validate every batch with mock + alpha7-only controls.",
    ]
    if use_radio:
        risks.append("[3H]-MLA waste must go to the licensed liquid scintillation waste; budget includes disposal cost.")
    if total < consumables_total:
        risks.append(
            f"Budget cap (${budget_cap:,.0f}) is below itemised consumables (${consumables_total:,.0f});"
            " consider deferring α-bungarotoxin imaging or lowering n until cap is raised."
        )

    plan: dict[str, Any] = {
        "title": title,
        "hypothesisSummary": (
            f"Test: {question} Compared in HEK293T against alpha7-only and RIC-3-only "
            "controls, using surface α-bungarotoxin staining plus "
            + " + ".join([a for a in [
                "TEVC ACh dose–response" if use_tevc else None,
                "patch-clamp ACh dose–response" if use_patch and not use_tevc else None,
                "[3H]-MLA saturation binding" if use_radio else None,
            ] if a])
            + " as readouts."
        ),
        "protocolOriginNote": (
            "Protocol derived from the in-house X-tremeGENE 9 96-well transfection SOP and "
            "alpha7-relevant electrophysiology/binding assays, parameterised by your planner inputs "
            "(budget cap, timeline, instruments, stocked materials)."
        ),
        "protocol": protocol,
        "materials": materials,
        "budget": {
            "lines": budget_lines,
            "total": round(total, 2),
            "currency": "USD",
            "assumptions": assumptions,
        },
        "timeline": timeline,
        "validation": {
            "primaryEndpoints": primary_endpoints or [
                "Surface alpha7 expression: α-bungarotoxin-AF488 mean intensity (per field).",
            ],
            "successCriteria": success or [
                "alpha7+RIC-3 wells show mean α-bungarotoxin signal ≥1.4× alpha7-only with p<0.05 (Welch's t-test, n≥4 fields × 6 wells).",
            ],
            "controls": controls,
            "analyticalMethods": methods,
        },
        "staffingNotes": staffing,
        "riskMitigation": risks,
        "agentMetadata": {"agent2Used": False, "agent4Used": False},
    }
    return plan


def _render_execution_report_fallback(plan: dict[str, Any], req: ExperimentPlanRequest) -> str:
    """Build a clear, non-vague, executable wet-lab report from a plan dict."""
    ctx = _planner_dict(req)
    lines: list[str] = []
    lines.append(f"# Wet-Lab Execution Report — {plan.get('title', req.question.strip())}")
    lines.append("")
    lines.append("## 1. Hypothesis under test")
    lines.append(plan.get("hypothesisSummary", req.question.strip()))
    lines.append("")
    lines.append("## 2. Constraints from planner")
    lines.append(f"- Budget cap: ${ctx['budget']:,.0f}")
    lines.append(f"- Timeline: {ctx['weeks']} weeks")
    lines.append(
        "- Instruments already available: "
        + (", ".join(ctx["instruments"]) if ctx["instruments"] else "none specified")
    )
    lines.append(
        "- Materials already on hand: "
        + (", ".join(ctx["materials_have"]) if ctx["materials_have"] else "none specified")
    )
    lines.append(
        "- Materials still required: "
        + (", ".join(ctx["materials_need"]) if ctx["materials_need"] else "none specified")
    )
    lines.append(
        "- Preferred assays: "
        + (", ".join(ctx["assays"]) if ctx["assays"] else "default suite")
    )
    if ctx["notes"]:
        lines.append(f"- Additional planner notes: {ctx['notes']}")
    lines.append("")
    lines.append("## 3. Materials & supply chain (catalog-priced)")
    lines.append("")
    lines.append("| # | Item | Supplier | Catalog | Qty | Line cost |")
    lines.append("|---|------|----------|---------|-----|-----------|")
    for i, m in enumerate(plan.get("materials") or [], start=1):
        line_total = m.get("lineTotal")
        cost = "already in lab" if (line_total in (0, 0.0, None)) and "already in lab" in str(m.get("quantity", "")) else (
            f"${float(line_total):,.0f}" if line_total not in (None,) else "TBD"
        )
        lines.append(
            f"| {i} | {m.get('name','')} | {m.get('supplier','')} | {m.get('catalogNumber','-')}"
            f" | {m.get('quantity','-')} | {cost} |"
        )
    lines.append("")
    budget = plan.get("budget") or {}
    lines.append("## 4. Budget summary")
    for line in budget.get("lines") or []:
        lines.append(
            f"- **{line.get('category','')}** — {line.get('description','')}"
            f" → ${float(line.get('amount', 0)):,.2f} {line.get('currency','USD')}"
        )
    lines.append(
        f"- **Total**: ${float(budget.get('total', 0)):,.2f} {budget.get('currency','USD')}"
    )
    for a in budget.get("assumptions") or []:
        lines.append(f"  - Assumption: {a}")
    lines.append("")
    lines.append("## 5. Timeline (Gantt-ready)")
    for t in plan.get("timeline") or []:
        lines.append(
            f"- **Wk {t.get('startWeek','?')}–{t.get('endWeek','?')}** · {t.get('phase','')}: "
            f"{t.get('description','')}"
        )
    lines.append("")
    lines.append("## 6. Step-by-step protocol (no vague steps)")
    for step in plan.get("protocol") or []:
        lines.append(
            f"### Step {step.get('stepNumber','?')}. {step.get('title','')}"
            + (f" _( {step.get('duration')} )_" if step.get("duration") else "")
        )
        lines.append(step.get("description", ""))
        if step.get("notes"):
            lines.append(f"> Note: {step['notes']}")
        lines.append("")
    val = plan.get("validation") or {}
    lines.append("## 7. Validation & QC")
    if val.get("primaryEndpoints"):
        lines.append("**Primary endpoints**")
        for x in val["primaryEndpoints"]:
            lines.append(f"- {x}")
    if val.get("successCriteria"):
        lines.append("**Success criteria**")
        for x in val["successCriteria"]:
            lines.append(f"- {x}")
    if val.get("controls"):
        lines.append("**Controls**")
        for x in val["controls"]:
            lines.append(f"- {x}")
    if val.get("analyticalMethods"):
        lines.append("**Analytical methods**")
        for x in val["analyticalMethods"]:
            lines.append(f"- {x}")
    lines.append("")
    if plan.get("staffingNotes"):
        lines.append("## 8. Staffing")
        for x in plan["staffingNotes"]:
            lines.append(f"- {x}")
        lines.append("")
    if plan.get("riskMitigation"):
        lines.append("## 9. Risk mitigation")
        for x in plan["riskMitigation"]:
            lines.append(f"- {x}")
        lines.append("")
    lines.append("## 10. Fulcrum deliverable checklist")
    lines.append("- [ ] Protocol PDF signed by PI")
    lines.append("- [ ] Materials sheet with PO numbers attached")
    lines.append("- [ ] Budget table approved by lab manager")
    lines.append("- [ ] Gantt chart in lab calendar")
    lines.append("- [ ] Pre-registered analysis plan committed to repo")
    return "\n".join(lines).rstrip() + "\n"


@app.post("/api/experiment-plan")
def generate_experiment_plan(req: ExperimentPlanRequest) -> dict[str, Any]:
    question = req.question.strip()
    if len(question) < 3:
        raise HTTPException(status_code=400, detail="question must be at least 3 characters")

    plan: dict[str, Any] | None = None
    agent2_used = False
    agent4_used = False
    fallback_reasons: list[str] = []

    if OPENAI_API_KEY:
        try:
            plan = _masterplan_from_inputs(req)
            agent2_used = True
        except Exception as exc:
            fallback_reasons.append(f"Agent2 plan: {exc}")

    if plan is None:
        plan = _masterplan_fallback(req)
    elif not isinstance(plan, dict) or not plan.get("protocol"):
        fallback_reasons.append("Agent2 plan was empty or malformed.")
        plan = _masterplan_fallback(req)

    report: str | None = None
    if OPENAI_API_KEY and agent2_used:
        try:
            report = agent4_generate_execution_report(plan, req)
            agent4_used = True
        except Exception as exc:
            fallback_reasons.append(f"Agent4 report: {exc}")

    if not report:
        report = _render_execution_report_fallback(plan, req)

    plan["executionReport"] = report
    metadata = plan.get("agentMetadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["agent2Used"] = agent2_used
    metadata["agent4Used"] = agent4_used
    if fallback_reasons:
        metadata["fallbackReasons"] = fallback_reasons
    plan["agentMetadata"] = metadata
    return plan


if __name__ == "__main__":
    import uvicorn

    _port = _env_int("PORT", 8000)
    uvicorn.run("main:app", host="127.0.0.1", port=_port, reload=True)
