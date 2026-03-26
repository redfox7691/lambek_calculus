#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
JOB_STORE = ROOT / "webapp" / "job_store"
JOB_STORE.mkdir(parents=True, exist_ok=True)

ARTIFACT_RE = re.compile(r"(/.+?\.(?:txt|png|svg|csv|tex|pdf))", re.IGNORECASE)


@dataclass
class Job:
    id: str
    kind: str
    command: list[str]
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._load_existing()

    def create(self, kind: str, command: list[str]) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, kind=kind, command=command)
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job)
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def list(self) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def update(self, job_id: str, **kwargs: Any) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in kwargs.items():
                setattr(job, key, value)
        self._persist(job)
        return job

    def _persist(self, job: Job) -> None:
        path = JOB_STORE / f"{job.id}.json"
        path.write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")

    def _load_existing(self) -> None:
        for path in sorted(JOB_STORE.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                job = Job(**payload)
                self._jobs[job.id] = job
            except Exception:
                continue


registry = JobRegistry()


class GenerateChordRequest(BaseModel):
    tonality: str = "C"
    target_chords: int = Field(default=8, ge=1)
    max_depth: int = Field(default=64, ge=0)
    branching_mode: str = Field(default="mixed", pattern="^(left|mixed)$")
    style_strength: float = Field(default=0.75, ge=0.0, le=1.0)
    temperature: float = Field(default=0.9, gt=0.0)
    modulation_strength: float = Field(default=0.25, ge=0.0, le=1.0)
    modulation_complexity: float = Field(default=0.5, ge=0.0, le=1.0)
    tonal_drift: float = Field(default=0.5, ge=0.0, le=1.0)
    initial_cadence_bias: float = Field(default=0.9, ge=0.0, le=1.0)
    basic_cadence_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    basic_cadence_mode: bool = False
    png: bool = True
    png_dpi: int = Field(default=900, ge=72)
    seed: int = 0


class CadenceStatsRequest(BaseModel):
    input_txt: str = "last_line_analysis/JazzStandards_all.txt"
    top_n: int = Field(default=20, ge=1)
    csv_out: str = "last_line_analysis/cadence_stats_web.csv"
    include_self: bool = False


class AnalyseRequest(BaseModel):
    standards_folder: str = "JazzStandards-main/JazzStandards"
    standard_name: str = ""
    custom_sequence: str = ""
    format: str = Field(default="txt", pattern="^(txt|tex)$")
    cadence_csv: str = "last_line_analysis/JazzStandards_all_cadence_web.csv"
    include_self: bool = False
    dpi: int = Field(default=600, ge=72)
    readable_dpi: int = Field(default=900, ge=72)


class RouteRequest(BaseModel):
    from_tonality: str
    to_tonality: str
    spelling: str = Field(default="auto", pattern="^(auto|flats|sharps)$")


class TableauRequest(BaseModel):
    sequence: str
    mode: str = Field(default="both", pattern="^(tree|arc|both)$")


app = FastAPI(title="Lambek Calculus Webapp", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(ROOT / "webapp" / "static")), name="static")


def _safe_rel(path_like: str) -> str:
    p = Path(path_like)
    if p.is_absolute():
        try:
            p.relative_to(ROOT)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Path must be under project root: {ROOT}") from exc
        return str(p)
    return str((ROOT / p).resolve().relative_to(ROOT))


def _find_standard_json(standards_folder: str, standard_name: str) -> Path:
    folder = ROOT / _safe_rel(standards_folder)
    candidate = folder / f"{standard_name}.json"
    if candidate.exists():
        return candidate
    lowered = standard_name.strip().lower()
    for p in folder.glob("*.json"):
        if p.stem.lower() == lowered:
            return p
    raise HTTPException(status_code=404, detail=f"Standard not found: {standard_name}")


def _extract_section_labels(song: dict[str, Any]) -> list[str]:
    sections = song.get("Sections", [])
    out: list[str] = []
    for idx, section in enumerate(sections):
        label = str(section.get("Label", f"S{idx + 1}"))
        base = f"{label}_{idx + 1}"
        out.append(base)

        endings = section.get("Endings", [])
        for eidx, ending in enumerate(endings, start=1):
            if isinstance(ending, dict) and str(ending.get("Chords", "")).strip():
                out.append(f"{base}_Ending{eidx}")

    if not out and str(song.get("Chords", "")).strip():
        out.append("Section_1")
    return out


def _tokenize_sequence_text(sequence: str) -> list[str]:
    tokens = [t.strip() for t in re.split(r"[\s|,]+", sequence) if t.strip()]
    return tokens


def _job_runner(job_id: str) -> None:
    job = registry.get(job_id)
    cmd = job.command
    registry.update(job_id, status="running", started_at=datetime.now(timezone.utc).isoformat())
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "MPLBACKEND": "Agg"},
        )
        out, err = proc.communicate()
        artifacts: list[str] = []
        for line in (out + "\n" + err).splitlines():
            for match in ARTIFACT_RE.finditer(line):
                raw_path = match.group(1).strip().rstrip(".,);:")
                path = Path(raw_path)
                if path.is_absolute() and path.exists():
                    artifacts.append(str(path))

        # ── post-process: convert any .tex proof trees → .svg ─────────────
        tex_to_svg = ROOT / "tex_to_svg.py"
        if tex_to_svg.exists():
            extra: list[str] = []
            for art in list(artifacts):
                if art.lower().endswith(".tex"):
                    svg_path = Path(art).with_suffix(".svg")
                    if not svg_path.exists():
                        try:
                            subprocess.run(
                                ["python3", str(tex_to_svg), art, "--out", str(svg_path)],
                                cwd=str(ROOT),
                                capture_output=True,
                                timeout=30,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    if svg_path.exists():
                        extra.append(str(svg_path))
            artifacts.extend(extra)

        registry.update(
            job_id,
            status="done" if proc.returncode == 0 else "error",
            finished_at=datetime.now(timezone.utc).isoformat(),
            return_code=proc.returncode,
            stdout=out,
            stderr=err,
            artifacts=sorted(set(artifacts)),
            error=None if proc.returncode == 0 else "Command failed",
        )
    except Exception as exc:  # noqa: BLE001
        registry.update(
            job_id,
            status="error",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )


def _submit(kind: str, cmd: list[str]) -> dict[str, Any]:
    job = registry.create(kind=kind, command=cmd)
    t = threading.Thread(target=_job_runner, args=(job.id,), daemon=True)
    t.start()
    return {"job_id": job.id, "status": job.status, "command": " ".join(shlex.quote(x) for x in cmd)}


@app.get("/")
def home() -> FileResponse:
    return FileResponse(ROOT / "webapp" / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/standards")
def list_standards(standards_folder: str = "JazzStandards-main/JazzStandards") -> dict[str, Any]:
    rel = _safe_rel(standards_folder)
    folder = ROOT / rel
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Invalid standards folder: {folder}")
    names = sorted(p.stem for p in folder.glob("*.json"))
    return {"standards_folder": str(folder), "count": len(names), "standards": names}


@app.get("/api/standards/sections")
def list_standard_sections(
    standards_folder: str = "JazzStandards-main/JazzStandards",
    standard_name: str = "",
) -> dict[str, Any]:
    if not standard_name.strip():
        raise HTTPException(status_code=400, detail="standard_name is required")
    std_json = _find_standard_json(standards_folder, standard_name)
    try:
        song = json.loads(std_json.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Cannot parse {std_json.name}: {exc}") from exc
    sections = _extract_section_labels(song)
    return {"standard_name": std_json.stem, "sections": sections, "count": len(sections)}


def _find_stats_csv() -> Path | None:
    """Locate the cadence stats CSV, checking several known filenames/locations."""
    candidates = [
        # Bundled copy always present in the repo (used for cloud deployment)
        ROOT / "webapp" / "data" / "cadence_stats_all.csv",
        ROOT / "last_line_analysis" / "JazzStandards_all_cadence.csv",
        ROOT / "last_line_analysis" / "cadence_stats_all.csv",
        ROOT.parent / "last_line_analysis" / "JazzStandards_all_cadence.csv",
        ROOT.parent / "last_line_analysis" / "cadence_stats_all.csv",
        # Walk up to the repo root (handles nested worktrees)
        ROOT.parents[2] / "last_line_analysis" / "JazzStandards_all_cadence.csv",
        ROOT.parents[2] / "last_line_analysis" / "cadence_stats_all.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@app.post("/api/jobs/generate-chords")
def create_generate_job(req: GenerateChordRequest) -> dict[str, Any]:
    script = ROOT / "generate_chords.py"
    cmd = [
        "python3",
        str(script),
        "--tonality",
        req.tonality,
        "--target-chords",
        str(req.target_chords),
        "--max-depth",
        str(req.max_depth),
        "--branching-mode",
        req.branching_mode,
        "--style-strength",
        str(req.style_strength),
        "--temperature",
        str(req.temperature),
        "--modulation-strength",
        str(req.modulation_strength),
        "--modulation-complexity",
        str(req.modulation_complexity),
        "--tonal-drift",
        str(req.tonal_drift),
        "--initial-cadence-bias",
        str(req.initial_cadence_bias),
        "--basic-cadence-strength",
        str(req.basic_cadence_strength),
        "--seed",
        str(req.seed),
    ]
    if req.basic_cadence_mode:
        cmd.append("--basic-cadence-mode")
    if req.png:
        cmd.extend(["--png", "--png-dpi", str(req.png_dpi)])
    stats_csv = _find_stats_csv()
    if stats_csv:
        cmd.extend(["--stats-csv", str(stats_csv)])
    return _submit("generate-chords", cmd)


@app.post("/api/jobs/cadence-stats")
def create_cadence_stats_job(req: CadenceStatsRequest) -> dict[str, Any]:
    script = ROOT / "cadence_stats.py"
    cmd = [
        "python3",
        str(script),
        "--input",
        _safe_rel(req.input_txt),
        "--top-n",
        str(req.top_n),
        "--csv-out",
        _safe_rel(req.csv_out),
    ]
    if req.include_self:
        cmd.append("--include-self")
    return _submit("cadence-stats", cmd)


@app.post("/api/jobs/analyse-standards")
def create_analyse_job(req: AnalyseRequest) -> dict[str, Any]:
    script = ROOT / "lambek_tree.py"
    custom_sequence = req.custom_sequence.strip()
    if custom_sequence:
        chords = _tokenize_sequence_text(custom_sequence)
        if not chords:
            raise HTTPException(status_code=400, detail="custom_sequence is empty after parsing")
        cmd = [
            "python3",
            str(script),
            "--sequence",
            *chords,
            "--out-dir",
            "tree_outputs",
            "--dpi",
            str(req.readable_dpi),
        ]
        return _submit("analyse-sequence", cmd)

    standards_dir_rel = _safe_rel(req.standards_folder)
    standard_name = req.standard_name.strip()
    if standard_name:
        cmd = [
            "python3",
            str(script),
            "--standard-readable",
            standard_name,
            "--standards-dir",
            standards_dir_rel,
            "--dpi",
            str(req.dpi),
            "--readable-dpi",
            str(req.readable_dpi),
            "--overwrite",
        ]
        return _submit("analyse-standard-readable", cmd)

    cmd = [
        "python3",
        str(script),
        "analyse",
        standards_dir_rel,
        "--format",
        req.format,
        "--cadence-csv",
        _safe_rel(req.cadence_csv),
    ]
    if req.include_self:
        cmd.append("--include-self")
    return _submit("analyse-standards", cmd)


@app.post("/api/jobs/route")
def create_route_job(req: RouteRequest) -> dict[str, Any]:
    script = ROOT / "route_explorer" / "route_app.py"
    out_dir = ROOT / "route_outputs"
    out_dir.mkdir(exist_ok=True)
    save_path = str(out_dir / f"route_{uuid.uuid4().hex[:8]}.png")
    cmd = [
        "python3", str(script),
        "--from", req.from_tonality,
        "--to", req.to_tonality,
        "--spelling", req.spelling,
        "--save", save_path,
        "--no-show",
    ]
    return _submit("route", cmd)


@app.post("/api/jobs/tableau")
def create_tableau_job(req: TableauRequest) -> dict[str, Any]:
    script = ROOT / "proof_tableau.py"
    chords = _tokenize_sequence_text(req.sequence)
    if not chords:
        raise HTTPException(status_code=400, detail="sequence is empty after parsing")
    out_dir = ROOT / "tableau_outputs"
    out_dir.mkdir(exist_ok=True)
    save_path = str(out_dir / f"tableau_{uuid.uuid4().hex[:8]}.png")
    cmd = [
        "python3", str(script),
        "--sequence", *chords,
        "--mode", req.mode,
        "--save", save_path,
        "--no-show",
    ]
    return _submit("tableau", cmd)


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [asdict(j) for j in registry.list()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return asdict(registry.get(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.get("/api/jobs/{job_id}/artifacts")
def list_job_artifacts(job_id: str) -> dict[str, Any]:
    try:
        job = registry.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return {"job_id": job.id, "artifacts": job.artifacts}


def _strip_cmd_wrappers(s: str) -> str:
    """Remove \\cmd{...} formatting wrappers using brace-depth counting, keep inner content."""
    cmds = ('textbf', 'mathbf', 'boldsymbol', 'text', 'scriptsize', 'normalsize', 'mbox')
    pattern = re.compile(r'\\(' + '|'.join(cmds) + r')\s*\{')
    result = []
    i = 0
    while i < len(s):
        m = pattern.search(s, i)
        if not m:
            result.append(s[i:])
            break
        result.append(s[i:m.start()])
        brace_pos = m.end() - 1  # position of opening {
        depth, j = 1, brace_pos + 1
        while j < len(s) and depth > 0:
            if s[j] == '{':
                depth += 1
            elif s[j] == '}':
                depth -= 1
            j += 1
        result.append(s[brace_pos + 1: j - 1])  # inner content without wrapper
        i = j
    return ''.join(result)


def _clean_label_arg(raw: str) -> str:
    """Convert LeftLabel/RightLabel content to plain text for MathJax bussproofs."""
    s = raw.replace('$', '')          # remove inline-math delimiters
    s = _strip_cmd_wrappers(s)        # strip \textbf{}, \boldsymbol{}, etc.
    # Replace LaTeX symbols with readable plain-text equivalents
    s = re.sub(r'\\backslash_', '\\\\', s)  # \backslash_L  →  \L  (consume underscore)
    s = s.replace('\\backslash', '\\')      # remaining \backslash  →  \
    s = re.sub(r'\\slash_', '/', s)         # \slash_L  →  /L  (consume underscore)
    s = s.replace('\\slash', '/')
    s = s.replace('\\flat', '♭')
    s = s.replace('\\sharp', '♯')
    s = s.replace('\\Box', '□')
    # Strip braces from subscripts: R_{SR_D}C  →  R_SR_DC
    s = re.sub(r'_\{([^}]+)\}', r'_\1', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _fix_labels(block: str) -> str:
    """Walk block char-by-char to extract and clean each LeftLabel/RightLabel arg."""
    result = []
    i = 0
    while i < len(block):
        # Look for \LeftLabel{ or \RightLabel{
        m = re.search(r'\\(LeftLabel|RightLabel)\{', block[i:])
        if not m:
            result.append(block[i:])
            break
        start = i + m.start()
        brace_start = i + m.end() - 1  # position of '{'
        result.append(block[i:start])
        result.append(f'\\{m.group(1)}{{')
        # Find matching closing brace
        depth = 1
        j = brace_start + 1
        while j < len(block) and depth > 0:
            if block[j] == '{':
                depth += 1
            elif block[j] == '}':
                depth -= 1
            j += 1
        # block[brace_start+1 : j-1] is the label content
        raw_content = block[brace_start + 1: j - 1]
        result.append(_clean_label_arg(raw_content))
        result.append('}')
        i = j
    return ''.join(result)


def _extract_prooftrees(tex_path: Path) -> list[str]:
    """Extract and preprocess bussproofs environments from a .tex file for MathJax."""
    text = tex_path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.findall(
        r'\\begin\{scprooftree\}\{[^}]*\}(.*?)\\end\{scprooftree\}|'
        r'\\begin\{prooftree\}(.*?)\\end\{prooftree\}',
        text, re.DOTALL
    )
    result = []
    for b1, b2 in blocks:
        block = b1 if b1.strip() else b2
        block = re.sub(r'\\def\\defaultHypSeparation\{[^}]*\}', '', block)
        # \sststile{d}{} → \vdash_{d}
        block = re.sub(r'\\sststile\{([^}]*)\}\{[^}]*\}', r'\\vdash_{\1}', block)
        # \MA macro
        block = block.replace(r'\MA', r'^{\mathrm{MA}^7}')
        # Remove \doubleLine
        block = re.sub(r'\\doubleLine\s*', '', block)
        # Clean label content so MathJax renders it correctly
        block = _fix_labels(block)
        block = f'\\begin{{prooftree}}{block}\\end{{prooftree}}'
        result.append(block.strip())
    return result


@app.get("/api/jobs/{job_id}/prooftrees")
def get_prooftrees(job_id: str, section: str = "") -> dict[str, Any]:
    try:
        job = registry.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    trees: list[str] = []
    for art in job.artifacts:
        if not art.lower().endswith(".tex"):
            continue
        if section:
            # Exact suffix match: "A_1" matches "Cute_A_1.tex" but NOT "Cute_A_1_Ending2.tex"
            stem = Path(art).stem.lower()
            sec = section.lower()
            if not re.search(r'(?:^|_)' + re.escape(sec) + r'$', stem):
                continue
        p = Path(art)
        if p.exists():
            trees.extend(_extract_prooftrees(p))
    return {"job_id": job.id, "prooftrees": trees}


@app.get("/api/jobs/{job_id}/download")
def download_artifact(job_id: str, path: str) -> FileResponse:
    try:
        job = registry.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc

    abs_path = Path(path).resolve()
    if str(abs_path) not in set(job.artifacts):
        raise HTTPException(status_code=400, detail="Artifact not in this job")
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media = "image/svg+xml" if abs_path.suffix.lower() == ".svg" else None
    return FileResponse(abs_path, media_type=media)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("LAMBEK_WEB_HOST", "0.0.0.0")
    # Render (and most cloud platforms) inject $PORT; fall back to LAMBEK_WEB_PORT then 8000
    port = int(os.environ.get("PORT") or os.environ.get("LAMBEK_WEB_PORT", "8000"))
    uvicorn.run("backend:app", host=host, port=port, reload=False)
