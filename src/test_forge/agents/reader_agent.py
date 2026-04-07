"""
Reader Agent — reads test case files and returns structured TestCase objects.

Supports:
- Excel (.xlsx, .xls)
- CSV (.csv)
- Word (.docx)
- PDF (.pdf)
- Markdown (.md)
- Plain text (.txt)

Two parsing strategies:
- Rule-based: Excel, CSV (fast, deterministic, no LLM)
- LLM-based:  Word, PDF (handles unstructured formats)
"""

import re
from pathlib import Path

import pandas as pd
import structlog

from src.test_forge.config.settings import get_settings
from src.test_forge.core.models import ReadResult, TestCase

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Column name aliases ────────────────────────────────────────────────────────
# People name columns differently — map all variations to standard names
COLUMN_ALIASES: dict[str, list[str]] = {
    "id": [
        "id",
        "test id",
        "test_id",
        "testid",
        "tc id",
        "tc_id",
        "case id",
        "case_id",
        "test case id",
        "no",
        "number",
        "#",
    ],
    "description": [
        "description",
        "desc",
        "title",
        "name",
        "test name",
        "test description",
        "summary",
        "test title",
        "objective",
    ],
    "steps": [
        "steps",
        "test steps",
        "step",
        "steps to execute",
        "steps to reproduce",
        "actions",
        "procedure",
        "test procedure",
    ],
    "expected_result": [
        "expected result",
        "expected_result",
        "expected",
        "expected outcome",
        "expected behavior",
        "expected behaviour",
        "result",
        "pass criteria",
        "acceptance criteria",
        "expected output",
    ],
    "preconditions": [
        "preconditions",
        "precondition",
        "pre-conditions",
        "pre conditions",
        "prerequisites",
        "pre-requisites",
        "setup",
        "test setup",
    ],
    "priority": ["priority", "severity", "importance", "criticality"],
    "category": [
        "category",
        "type",
        "module",
        "feature",
        "area",
        "test type",
        "component",
        "section",
    ],
    "url": ["url", "link", "page", "page url", "test url", "application url", "endpoint"],
}


def normalize_column_name(col: str) -> str | None:
    """
    Maps any column name variation to our standard field name.
    Returns None if no match found.
    """
    col_lower = col.lower().strip()
    for standard, aliases in COLUMN_ALIASES.items():
        if col_lower in aliases:
            return standard
    return None


# ── Excel / CSV parser (rule-based) ───────────────────────────────────────────
def parse_excel(file_path: str) -> ReadResult:
    """Parse Excel file — each row is one test case."""
    import pandas as pd

    log.info("reading_excel", path=file_path)

    try:
        df = pd.read_excel(file_path, dtype=str)
    except Exception as e:
        log.error("excel_read_error", path=file_path, error=str(e))
        return ReadResult(
            source_file=file_path,
            file_format="excel",
            test_cases=[],
            total_found=0,
            total_parsed=0,
            skipped=0,
            warnings=[f"Could not read file: {e}"],
        )

    return _parse_dataframe(df, file_path, "excel")


def parse_csv(file_path: str) -> ReadResult:
    """Parse CSV file — each row is one test case."""
    import pandas as pd

    log.info("reading_csv", path=file_path)

    try:
        df = pd.read_csv(file_path, dtype=str, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(file_path, dtype=str, encoding="latin-1")
    except Exception as e:
        log.error("csv_read_error", path=file_path, error=str(e))
        return ReadResult(
            source_file=file_path,
            file_format="csv",
            test_cases=[],
            total_found=0,
            total_parsed=0,
            skipped=0,
            warnings=[f"Could not read file: {e}"],
        )

    return _parse_dataframe(df, file_path, "csv")


def _parse_dataframe(df: "pd.DataFrame", file_path: str, fmt: str) -> ReadResult:
    """
    Shared logic for Excel and CSV.
    Maps columns to standard names, extracts test cases row by row.
    """
    import pandas as pd

    # Normalize column names
    col_map: dict[str, str] = {}
    for col in df.columns:
        standard = normalize_column_name(str(col))
        if standard:
            col_map[standard] = col

    log.info("column_mapping", mapped=col_map, original=list(df.columns))

    # Check required columns exist
    warnings = []
    if "description" not in col_map:
        warnings.append("No 'description' column found — will use empty string")
    if "steps" not in col_map:
        warnings.append("No 'steps' column found — will use empty list")
    if "expected_result" not in col_map:
        warnings.append("No 'expected result' column found — will use empty string")

    test_cases = []
    skipped = 0
    total = len(df)

    for idx, row in df.iterrows():
        row_num = idx + 2  # +2 because header is row 1, idx is 0-based

        # Skip completely empty rows
        if row.isna().all():
            skipped += 1
            continue

        def get_field(field: str, default: str = "", _row: "pd.Series" = row) -> str:
            col = col_map.get(field)
            if col is None:
                return default
            val = _row.get(col, default)
            if pd.isna(val):
                return default
            return str(val).strip()

        # Generate ID if not present
        tc_id = get_field("id") or f"TC{row_num:03d}"

        description = get_field("description")
        if not description:
            log.warning("skipping_row_no_description", row=row_num, id=tc_id)
            skipped += 1
            continue

        try:
            tc = TestCase(
                id=tc_id,
                description=description,
                steps=get_field("steps").splitlines() or [],  # type: ignore[arg-type]
                expected_result=get_field("expected_result"),
                preconditions=get_field("preconditions"),
                priority=get_field("priority", "medium"),
                category=get_field("category", "general"),
                url=get_field("url"),
                raw_text=str(row.to_dict()),
            )
            test_cases.append(tc)
            log.info("parsed_test_case", id=tc_id, steps=len(tc.steps))

        except Exception as e:
            log.warning("row_parse_error", row=row_num, error=str(e))
            warnings.append(f"Row {row_num}: {e}")
            skipped += 1

    return ReadResult(
        source_file=file_path,
        file_format=fmt,
        test_cases=test_cases,
        total_found=total,
        total_parsed=len(test_cases),
        skipped=skipped,
        warnings=warnings,
    )


# ── Markdown / Text parser (rule-based) ───────────────────────────────────────
def parse_markdown(file_path: str) -> ReadResult:
    log.info("reading_markdown", path=file_path)

    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return ReadResult(
            source_file=file_path,
            file_format="markdown",
            test_cases=[],
            total_found=0,
            total_parsed=0,
            skipped=0,
            warnings=[f"Could not read file: {e}"],
        )

    # Split by test case headers
    blocks = re.split(r"\n#{1,3}\s+", content)
    blocks = [b.strip() for b in blocks if b.strip()]

    test_cases: list[TestCase] = []
    skipped = 0
    warnings: list[str] = []

    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue

        header = lines[0].strip()

        # ── Skip blocks that are just document titles ──────────────────────────
        has_tc_pattern = bool(re.match(r"^(TC\d+|Test\s*\d+)\s*[-—:]\s*.+$", header, re.IGNORECASE))
        has_steps = "steps" in block.lower()
        has_expected = "expected" in block.lower()

        if not has_tc_pattern and not (has_steps or has_expected):
            log.info("skipping_non_test_block", header=header[:40])
            skipped += 1
            continue

        id_match = re.match(r"^(TC\d+|Test\s*\d+)\s*[-—:]\s*(.+)$", header, re.IGNORECASE)

        if id_match:
            tc_id = id_match.group(1).upper().replace(" ", "")
            description = id_match.group(2).strip()
        else:
            tc_id = f"TC{len(test_cases) + 1:03d}"
            description = header

        block_text = "\n".join(lines[1:])
        block_text_normalized = block_text.replace("\r\n", "\n").replace("\r", "\n")

        def extract_field(pattern: str, text: str = block_text_normalized) -> str:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
            return ""

        preconditions = extract_field(
            r"\*{0,2}preconditions?\*{0,2}:?\s*\*{0,2}\s*(.+?)(?=\n\*\*|\Z)"
        )
        expected_result = extract_field(
            r"\*{0,2}expected(?:\s+result)?\*{0,2}:?\s*\*{0,2}\s*(.+?)(?=\n---|\Z)"
        )
        priority = extract_field(r"\*{0,2}priority\*{0,2}:?\s*\*{0,2}\s*(\w+)")
        category = extract_field(r"\*{0,2}category\*{0,2}:?\s*\*{0,2}\s*(.+?)(?=\n|\Z)")
        url = extract_field(r"\*{0,2}url\*{0,2}:?\s*\*{0,2}\s*(https?://\S+)")

        # ── Extract steps line by line ─────────────────────────────────────────
        # Simpler approach — iterate lines, collect numbered lines after **Steps:**
        steps_list: list[str] = []
        in_steps_section = False

        for line in block_text_normalized.split("\n"):
            line_stripped = line.strip()

            # Detect start of steps section
            if re.match(
                r"\*{0,2}steps?\*{0,2}:?\*{0,2}\s*$|steps\s*:", line_stripped, re.IGNORECASE
            ):
                in_steps_section = True
                continue

            # Detect end of steps section — next ** section starts
            if in_steps_section and re.match(r"\*\*.+\*\*", line_stripped):
                in_steps_section = False

            # Collect numbered step lines
            if in_steps_section:
                step_match = re.match(r"^\d+[\.\)]\s*(.+)$", line_stripped)
                if step_match:
                    steps_list.append(step_match.group(1).strip())

        # ── Clean markdown bold markers from extracted values ──────────────────
        def clean(s: str) -> str:
            return re.sub(r"\*+", "", s).strip().rstrip("-").strip()

        if not description:
            skipped += 1
            continue

        try:
            tc = TestCase(
                id=tc_id,
                description=description,
                steps=steps_list,
                expected_result=clean(expected_result),
                preconditions=clean(preconditions),
                priority=clean(priority) or "medium",
                category=clean(category) or "general",
                url=url,
                raw_text=block,
            )
            test_cases.append(tc)
            log.info("parsed_test_case", id=tc_id, steps=len(tc.steps))

        except Exception as e:
            log.warning("block_parse_error", error=str(e))
            warnings.append(f"Block '{tc_id}': {e}")
            skipped += 1

    return ReadResult(
        source_file=file_path,
        file_format="markdown",
        test_cases=test_cases,
        total_found=len(blocks),
        total_parsed=len(test_cases),
        skipped=skipped,
        warnings=warnings,
    )


# ── Word / PDF parser (LLM-based) ─────────────────────────────────────────────
def parse_word(file_path: str) -> ReadResult:
    """Parse Word document using python-docx then LLM for structure."""
    log.info("reading_word", path=file_path)

    try:
        from docx import Document

        doc = Document(file_path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())

        # Also extract tables
        for table in doc.tables:
            for row in table.rows:
                text += "\n" + " | ".join(cell.text.strip() for cell in row.cells)

    except Exception as e:
        return ReadResult(
            source_file=file_path,
            file_format="word",
            test_cases=[],
            total_found=0,
            total_parsed=0,
            skipped=0,
            warnings=[f"Could not read Word file: {e}"],
        )

    return _parse_with_llm(text, file_path, "word")


def parse_pdf(file_path: str) -> ReadResult:
    """Parse PDF using pypdf then LLM for structure."""
    log.info("reading_pdf", path=file_path)

    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
    except Exception as e:
        return ReadResult(
            source_file=file_path,
            file_format="pdf",
            test_cases=[],
            total_found=0,
            total_parsed=0,
            skipped=0,
            warnings=[f"Could not read PDF file: {e}"],
        )

    return _parse_with_llm(text, file_path, "pdf")


def _parse_with_llm(text: str, file_path: str, fmt: str) -> ReadResult:
    """
    Uses LLM to extract structured test cases from unstructured text.
    Returns structured TestCase objects.
    """
    import json

    from langchain_core.messages import HumanMessage, SystemMessage

    from src.test_forge.core.llm import get_llm

    log.info("llm_parsing", format=fmt, text_length=len(text))

    system = SystemMessage(
        content="""You are a test case parser.
Extract all test cases from the provided text and return them as a JSON array.

Each test case must have these fields:
- id: string (e.g. "TC001", generate if not present)
- description: string (what the test verifies)
- steps: array of strings (ordered steps)
- expected_result: string (what should happen)
- preconditions: string (what must be true before test, empty string if none)
- priority: string (high, medium, or low — default medium)
- category: string (e.g. authentication, checkout — default general)
- url: string (URL under test, empty string if not mentioned)

Return ONLY a valid JSON array. No markdown, no explanation, no backticks.
Example: [{"id": "TC001", "description": "...", "steps": ["step1", "step2"], ...}]
"""
    )

    user = HumanMessage(content=f"Extract test cases from this text:\n\n{text[:8000]}")

    warnings: list[str] = []
    test_cases: list[TestCase] = []

    try:
        llm = get_llm()
        response = llm.invoke([system, user])

        # ── Handle str | list response.content ────────────────────────────────
        raw_content = response.content
        if isinstance(raw_content, str):
            content = raw_content.strip()
        elif isinstance(raw_content, list) and raw_content:
            first = raw_content[0]
            content = first.strip() if isinstance(first, str) else str(first)
        else:
            content = ""

        # Strip markdown code fences if present
        content = re.sub(r"```(?:json)?\s*", "", content)
        content = content.strip().strip("`")

        data = json.loads(content)

        for item in data:
            try:
                tc = TestCase(**item)
                test_cases.append(tc)
                log.info("llm_parsed_test_case", id=tc.id)
            except Exception as e:
                log.warning("llm_item_parse_error", error=str(e))
                warnings.append(f"Could not parse item: {e}")

    except json.JSONDecodeError as e:
        warnings.append(f"LLM returned invalid JSON: {e}")
        log.error("llm_json_error", error=str(e))
    except Exception as e:
        warnings.append(f"LLM parsing failed: {e}")
        log.error("llm_parse_error", error=str(e))

    return ReadResult(
        source_file=file_path,
        file_format=fmt,
        test_cases=test_cases,
        total_found=len(test_cases),
        total_parsed=len(test_cases),
        skipped=len(warnings),
        warnings=warnings,
    )


# ── Reader Agent ───────────────────────────────────────────────────────────────
class ReaderAgent:
    """
    Reads test case files and returns structured TestCase objects.
    Automatically detects file format and uses the right parser.
    """

    PARSERS = {
        ".xlsx": parse_excel,
        ".xls": parse_excel,
        ".csv": parse_csv,
        ".docx": parse_word,
        ".pdf": parse_pdf,
        ".md": parse_markdown,
        ".txt": parse_markdown,
    }

    def run(self, file_path: str) -> ReadResult:
        """
        Read a test case file and return structured results.

        Args:
            file_path: Path to Excel, CSV, Word, PDF, or Markdown file

        Returns:
            ReadResult with list of TestCase objects and parsing metadata
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        # ── Check format first ─────────────────────────────────────────────────────
        # Check format before existence so unsupported formats get the right message
        parser = self.PARSERS.get(ext)
        if parser is None:
            return ReadResult(
                source_file=file_path,
                file_format="unknown",
                test_cases=[],
                total_found=0,
                total_parsed=0,
                skipped=0,
                warnings=[
                    f"Unsupported file format: '{ext}'. "
                    f"Supported formats: {', '.join(self.PARSERS.keys())}"
                ],
            )

        # ── Then check file exists ─────────────────────────────────────────────────
        if not path.exists():
            return ReadResult(
                source_file=file_path,
                file_format=ext.lstrip("."),
                test_cases=[],
                total_found=0,
                total_parsed=0,
                skipped=0,
                warnings=[f"File not found: {file_path}"],
            )

        log.info("reader_agent_start", file=str(path.name), format=ext)

        result = parser(str(path))

        log.info(
            "reader_agent_complete",
            file=str(path.name),
            parsed=result.total_parsed,
            skipped=result.skipped,
            warnings=len(result.warnings),
        )

        return result
