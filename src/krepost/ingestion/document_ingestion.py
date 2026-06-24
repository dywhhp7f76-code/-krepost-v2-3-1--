from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional

import yaml
from loguru import logger
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Инициализация
# ═══════════════════════════════════════════════════════════════════════════

_LOG_SINK_ID: Optional[int] = None


def init_logging(log_dir: Path = Path("data/logs")) -> None:
    # P2: защита от дубля sink при повторном вызове
    global _LOG_SINK_ID
    log_dir.mkdir(parents=True, exist_ok=True)
    if _LOG_SINK_ID is not None:
        try:
            logger.remove(_LOG_SINK_ID)
        except ValueError:
            pass
    _LOG_SINK_ID = logger.add(log_dir / "ingestion.log", rotation="10 MB",
                              level="INFO", enqueue=True)


# ═══════════════════════════════════════════════════════════════════════════
# События
# ═══════════════════════════════════════════════════════════════════════════

class IngestEventLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class IngestEventType(str, Enum):
    FILE_INGESTED = "file_ingested"
    FILE_SKIPPED = "file_skipped"
    FILE_FAILED = "file_failed"
    BATCH_DONE = "batch_done"
    BATCH_HIGH_FAIL_RATE = "batch_high_fail_rate"
    LARGE_FILE_DETECTED = "large_file_detected"
    VAULT_UNAVAILABLE = "vault_unavailable"
    DISK_FULL = "disk_full"
    OCR_FALLBACK_USED = "ocr_fallback_used"
    ENCODING_FALLBACK = "encoding_fallback"
    FRONTMATTER_OVERRIDDEN = "frontmatter_overridden"


@dataclass
class IngestEvent:
    level: IngestEventLevel
    type: IngestEventType
    message: str
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


def emit_event(event: IngestEvent, callback: Optional[Callable[[IngestEvent], None]]) -> None:
    """
    Локальные логи + внешний callback. ВАЖНО: callback вызывается синхронно,
    в т.ч. из потока executor — он ДОЛЖЕН быть sync и потокобезопасным
    (для async-доставки в Telegram оборачивай на стороне monitoring.py).
    """
    msg = f"[{event.level.value.upper()}] {event.type.value}: {event.message}"
    if event.level == IngestEventLevel.GREEN:
        logger.info(msg)
    elif event.level == IngestEventLevel.YELLOW:
        logger.warning(msg)
    else:
        logger.error(msg)
    if callback is not None:
        try:
            callback(event)
        except Exception:
            logger.exception("on_event callback failed")


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic-модели
# ═══════════════════════════════════════════════════════════════════════════

IngestStatus = Literal["success", "skipped", "failed"]


class IngestResult(BaseModel):
    source_path: str
    output_path: str
    file_type: str
    status: IngestStatus           # технический результат ingest
    chars: int = 0
    duration: float = 0.0
    error: Optional[str] = None
    quarantine: bool = True        # policy-флаг для downstream (всегда True для external)


class IngestReport(BaseModel):
    total: int
    success: int
    skipped: int
    failed: int
    duration: float
    results: List[IngestResult]

    @property
    def fail_rate(self) -> float:
        return self.failed / self.total if self.total else 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Утилиты
# ═══════════════════════════════════════════════════════════════════════════

SKIP_DIR_PARTS = {".git", ".obsidian", "node_modules", "__pycache__", ".venv", ".idea", ".DS_Store"}

# Security-поля frontmatter, которые НИКОГДА не берутся из пользовательского
# документа — всегда задаются системой (P0-1).
SECURITY_FM_FIELDS = {"source", "quarantine", "ingested", "content_sha256", "source_path"}


def streaming_hash(path: Path, chunk_size: int = 65536) -> str:
    """SHA-256 без загрузки файла целиком. Буфер 64KB (P2: быстрее на NVMe)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, content: str) -> None:
    """Запись через temp + fsync + atomic rename (P1-4: защита от пустого файла при power-loss)."""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def detect_and_read_text(path: Path,
                         on_event: Optional[Callable[[IngestEvent], None]] = None) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "windows-1251", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(raw).best()
        if result:
            return str(result)
    except ImportError:
        pass
    # P2: явная телеметрия о деградации (U+FFFD ломает эмбеддинги)
    emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.ENCODING_FALLBACK,
                           message=f"Decode с заменой символов: {path.name}",
                           payload={"path": str(path)}), on_event)
    return raw.decode("utf-8", errors="replace")


def _parse_existing_frontmatter(content: str) -> tuple[dict, str]:
    """
    Строгий парс frontmatter (P0-1, P2): только если контент начинается с '---\\n'
    И есть закрывающий '---'. Возвращает (поля, тело_без_frontmatter).
    Если frontmatter нет/битый — ({}, исходный_контент).
    """
    stripped = content.lstrip("\ufeff")  # снять BOM
    if not stripped.startswith("---"):
        return {}, content
    # ищем закрывающий разделитель
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", stripped, re.DOTALL)
    if not m:
        return {}, content
    try:
        parsed = yaml.safe_load(m.group(1)) or {}
        if not isinstance(parsed, dict):
            return {}, content
    except yaml.YAMLError:
        return {}, content
    return parsed, m.group(2)


def build_frontmatter(source_path: Path, relative_path: str, content_body: str,
                      content_hash: str, existing: Optional[dict] = None) -> str:
    """
    Собирает frontmatter. Security-поля ВСЕГДА системные (P0-1): даже если в
    existing пришли source:internal/quarantine:false — они перезаписываются.
    Несекьюрные поля пользователя (если были) сохраняются.
    """
    existing = existing or {}

    title = source_path.stem.replace("-", " ").replace("_", " ").title()
    h1 = re.search(r'^#\s+(.+)', content_body, re.MULTILINE)
    if h1:
        title = h1.group(1).strip()
    if isinstance(existing.get("title"), str) and existing["title"].strip():
        title = existing["title"].strip()

    # P2: теги — буквы И цифры (rfc2119 больше не теряется)
    tags = [w.lower() for w in re.findall(r'\b[\wа-яА-Я]{4,}\b', source_path.stem)][:5]
    if isinstance(existing.get("tags"), list):
        tags = [str(t) for t in existing["tags"]][:10] or tags

    # сохраняем безопасные пользовательские поля, выкидывая security-критичные
    safe_user = {k: v for k, v in existing.items()
                 if k not in SECURITY_FM_FIELDS and k not in ("title", "tags", "date", "ingestion_date")}

    fm_dict = {
        "title": title,
        "tags": tags or ["ingested"],
        # P2: doc-date сохраняется при re-ingest, ingestion_date обновляется
        "date": existing.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "ingestion_date": datetime.now().strftime("%Y-%m-%d"),
        # --- SECURITY-поля, всегда системные ---
        "source": "external",
        "source_format": source_path.suffix.lstrip("."),
        "source_path": str(relative_path),
        "content_sha256": content_hash[:16],
        "ingested": True,
        "quarantine": True,
    }
    fm_dict.update(safe_user)  # безопасные поля не могут переопределить security
    for k in SECURITY_FM_FIELDS:
        if k == "source":
            fm_dict[k] = "external"
        elif k == "quarantine":
            fm_dict[k] = True
        elif k == "ingested":
            fm_dict[k] = True
    yaml_block = yaml.safe_dump(fm_dict, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_block}---\n\n"


def _sanitize_and_attach_frontmatter(content: str, source_path: Path, relative_path: str,
                                     content_hash: str,
                                     on_event: Optional[Callable[[IngestEvent], None]] = None) -> str:
    """
    P0-1: единая точка — всегда выдаёт документ с системным frontmatter.
    Пользовательский frontmatter парсится, но security-поля перезаписываются.
    """
    existing, body = _parse_existing_frontmatter(content)
    if existing:
        # были попытки задать security-поля вручную? — сигнал
        if any(k in existing for k in SECURITY_FM_FIELDS):
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW,
                                   type=IngestEventType.FRONTMATTER_OVERRIDDEN,
                                   message=f"Перезаписаны security-поля frontmatter в {source_path.name}",
                                   payload={"path": str(source_path)}), on_event)
    fm = build_frontmatter(source_path, relative_path, body, content_hash, existing=existing)
    return fm + body


# ═══════════════════════════════════════════════════════════════════════════
# Extractors
# ═══════════════════════════════════════════════════════════════════════════

MAX_PDF_PAGES = 2000
MAX_CONTENT_CHARS = 2_000_000


def _iter_docx_blocks(doc):
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield ("paragraph", Paragraph(child, doc))
        elif child.tag == qn("w:tbl"):
            yield ("table", Table(child, doc))


def _docx_heading_level(paragraph) -> Optional[int]:
    """
    P1-3: уровень заголовка по builtin style_id (не по локализованному имени).
    Встроенные стили Word имеют style_id 'Heading1'..'Heading9' независимо
    от языка интерфейса ('Заголовок 1', 'Titre 1' и т.д.).
    """
    style = paragraph.style
    if style is None:
        return None
    sid = getattr(style, "style_id", "") or ""
    m = re.match(r"Heading(\d+)", sid)
    if m:
        return min(int(m.group(1)), 6)
    # запасной путь — английское имя
    name = style.name or ""
    m2 = re.match(r"Heading (\d+)", name)
    if m2:
        return min(int(m2.group(1)), 6)
    return None


def extract_pdf(path: Path, enable_ocr: bool = False,
                on_event: Optional[Callable[[IngestEvent], None]] = None) -> str:
    import fitz
    pages_text: List[str] = []
    has_empty_pages = False
    with fitz.open(str(path)) as doc:
        n_pages = len(doc)
        if n_pages > MAX_PDF_PAGES:
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.LARGE_FILE_DETECTED,
                       message=f"PDF {path.name}: {n_pages} страниц > лимит {MAX_PDF_PAGES}, обрезано",
                       payload={"path": str(path), "pages": n_pages}), on_event)
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break
            text = page.get_text("text").strip()
            if not text and enable_ocr:
                text = _ocr_pdf_page(page)
                if text:
                    emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.OCR_FALLBACK_USED,
                               message=f"OCR использован для {path.name} стр.{i+1}",
                               payload={"path": str(path), "page": i + 1}), on_event)
            if text:
                pages_text.append(f"<!-- Page {i+1} -->\n{text}")
            else:
                has_empty_pages = True
    if has_empty_pages and not enable_ocr:
        logger.warning(f"{path.name}: пустые страницы, OCR выключен")
    final = "\n\n".join(pages_text)
    if len(final) > MAX_CONTENT_CHARS:
        final = final[:MAX_CONTENT_CHARS] + "\n\n[truncated]"
    return final


def _ocr_pdf_page(page) -> str:
    try:
        import pytesseract
        from PIL import Image
        import io
        pix = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img, lang="rus+eng").strip()
    except ImportError:
        logger.debug("pytesseract not installed — OCR disabled")
        return ""
    except Exception as e:
        # P2: TesseractNotFoundError → явный лог про бинарь
        if e.__class__.__name__ == "TesseractNotFoundError":
            logger.error("Tesseract binary не найден в системе (brew install tesseract)")
        else:
            logger.exception("OCR failed for page")
        return ""


def extract_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    parts: List[str] = []
    for block_type, block in _iter_docx_blocks(doc):
        if block_type == "paragraph":
            text = block.text.strip()
            if not text:
                continue
            level = _docx_heading_level(block)   # P1-3
            style = block.style.name if block.style else ""
            if level is not None:
                parts.append(f"{'#' * level} {text}")
            elif "List" in style:
                parts.append(f"- {text}")
            else:
                parts.append(text)
        elif block_type == "table":
            rows = []
            for i, row in enumerate(block.rows):
                # P2: \n в ячейке ломает markdown-таблицу → <br>
                cells = [c.text.strip().replace("|", "\\|").replace("\n", "<br>") for c in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
                if i == 0:
                    rows.append("|" + "---|" * len(cells))
            if rows:
                parts.append("\n".join(rows))
    return "\n\n".join(parts)


def extract_txt(path: Path) -> str:
    return detect_and_read_text(path)


def extract_md(path: Path) -> str:
    return detect_and_read_text(path)


def extract_html(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError("HTML support requires: pip install beautifulsoup4")
    html = detect_and_read_text(path)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return soup.get_text(separator="\n").strip()


def extract_epub(path: Path) -> str:
    try:
        import ebooklib
        from ebooklib import epub
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError("EPUB support requires: pip install ebooklib beautifulsoup4")
    book = epub.read_epub(str(path))
    parts: List[str] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


EXTRACTORS = {
    ".pdf": extract_pdf, ".docx": extract_docx, ".txt": extract_txt,
    ".md": extract_md, ".html": extract_html, ".htm": extract_html, ".epub": extract_epub,
}


# ═══════════════════════════════════════════════════════════════════════════
# DocumentIngestion
# ═══════════════════════════════════════════════════════════════════════════

class DocumentIngestion:
    DEFAULT_BASE_DIR = Path("inbox")
    DEFAULT_VAULT_DIR = Path("vault")
    INGESTED_SUBDIR = "ingested"
    MAX_FILE_SIZE_MB = 100          # P0-2-related: понижен с 500
    BATCH_HIGH_FAIL_THRESHOLD = 0.5
    DEFAULT_MAX_CONCURRENT = 4      # P1-5

    def __init__(self, base_dir=DEFAULT_BASE_DIR, vault_dir=DEFAULT_VAULT_DIR,
                 hashes_path=Path("data/ingestion_hashes.json"), max_file_size_mb=MAX_FILE_SIZE_MB,
                 enable_ocr=False, on_event=None, on_note_changed=None,
                 max_concurrent=DEFAULT_MAX_CONCURRENT, loop=None):
        self.base_dir = base_dir.resolve()
        self.vault_dir = vault_dir.resolve()
        self.ingested_root = self.vault_dir / self.INGESTED_SUBDIR
        self.ingested_root.mkdir(parents=True, exist_ok=True)
        self.hashes_path = hashes_path
        self.hashes_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_file_size_mb = max_file_size_mb
        self.enable_ocr = enable_ocr
        self.on_event = on_event
        self.on_note_changed = on_note_changed
        self.max_concurrent = max_concurrent
        self._loop = loop  # для async on_note_changed из потока (P0-3)
        self.hashes: Dict[str, str] = self._load_hashes()
        self._hashes_dirty = False
        self._hashes_lock = threading.Lock()  # P1-1: threading, не asyncio

    # ── Hashes ──────────────────────────────────────────────────────────────

    def _load_hashes(self) -> Dict[str, str]:
        if not self.hashes_path.exists():
            return {}
        try:
            return json.loads(self.hashes_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load hashes — starting fresh")
            return {}

    def _save_hashes_now(self) -> None:
        try:
            with self._hashes_lock:
                snapshot = dict(self.hashes)
                self._hashes_dirty = False
            atomic_write(self.hashes_path, json.dumps(snapshot, indent=2, ensure_ascii=False))
        except Exception:
            logger.exception("Failed to save hashes")

    # ── Paths (P0-2) ─────────────────────────────────────────────────────────

    def _path_tag(self, source: Path) -> str:
        """Короткий хеш абсолютного пути — разводит одноимённые файлы вне base_dir."""
        return hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:8]

    def _relative_key(self, source: Path) -> str:
        try:
            return str(source.resolve().relative_to(self.base_dir))
        except ValueError:
            # P0-2: вне base_dir — имя + хеш пути, иначе коллизия ключей
            return f"{source.stem}__{self._path_tag(source)}{source.suffix}"

    def _output_path(self, source: Path) -> Path:
        try:
            rel = source.resolve().relative_to(self.base_dir).with_suffix(".md")
        except ValueError:
            # P0-2: вне base_dir — имя + хеш пути, иначе перезатир в vault
            rel = Path(f"{source.stem}__{self._path_tag(source)}.md")
        return self.ingested_root / rel

    # ── on_note_changed dispatch (P0-3) ──────────────────────────────────────

    def _dispatch_note_changed(self, out_path_str: str) -> None:
        cb = self.on_note_changed
        if cb is None:
            return
        try:
            if asyncio.iscoroutinefunction(cb):
                if self._loop is not None and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(cb(out_path_str), self._loop)
                else:
                    logger.warning("async on_note_changed, но loop недоступен — пропуск")
            else:
                cb(out_path_str)
        except Exception:
            logger.exception("on_note_changed callback failed")

    # ── Core ──────────────────────────────────────────────────────────────────

    def _ingest_file_sync(self, path: Path) -> IngestResult:
        start = time.time()
        suffix = path.suffix.lower()
        path_str = str(path)
        # P0-4: всё тело под try — один битый файл не рушит batch
        try:
            if not path.exists():
                emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.FILE_FAILED,
                           message=f"File not found: {path.name}", payload={"path": path_str}), self.on_event)
                return IngestResult(source_path=path_str, output_path="", file_type=suffix,
                                    status="failed", error="File not found", duration=round(time.time()-start,2))
            if suffix not in EXTRACTORS:
                emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.FILE_SKIPPED,
                           message=f"Unsupported format: {suffix}", payload={"path": path_str}), self.on_event)
                return IngestResult(source_path=path_str, output_path="", file_type=suffix,
                                    status="failed", error=f"Unsupported format: {suffix}", duration=round(time.time()-start,2))

            size_mb = path.stat().st_size / (1024 * 1024)
            if size_mb > self.max_file_size_mb:
                emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.LARGE_FILE_DETECTED,
                           message=f"Файл {path.name} {size_mb:.0f} МБ > лимит {self.max_file_size_mb} МБ",
                           payload={"path": path_str, "size_mb": size_mb}), self.on_event)
                return IngestResult(source_path=path_str, output_path="", file_type=suffix,
                                    status="failed", error=f"File too large: {size_mb:.0f} MB", duration=round(time.time()-start,2))

            file_hash = streaming_hash(path)
            key = self._relative_key(path)
            out_path = self._output_path(path)

            # P1-1: потокобезопасное чтение
            with self._hashes_lock:
                already = (self.hashes.get(key) == file_hash)
            if already and out_path.exists():
                emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.FILE_SKIPPED,
                           message=f"Skip (unchanged): {path.name}", payload={"path": path_str}), self.on_event)
                return IngestResult(source_path=path_str, output_path=str(out_path), file_type=suffix,
                                    status="skipped", duration=round(time.time()-start,2))

            # Extract
            try:
                extractor = EXTRACTORS[suffix]
                if suffix == ".pdf":
                    content = extractor(path, enable_ocr=self.enable_ocr, on_event=self.on_event)
                else:
                    content = extractor(path)
            except ImportError as e:
                return IngestResult(source_path=path_str, output_path="", file_type=suffix,
                                    status="failed", error=f"Optional lib missing: {e}", duration=round(time.time()-start,2))

            if not content.strip():
                return IngestResult(source_path=path_str, output_path="", file_type=suffix,
                                    status="failed", error="Empty content (OCR disabled?)", duration=round(time.time()-start,2))

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            was_rewrite = out_path.exists()   # P1/P2: invalidate только при перезаписи

            # P0-1: всегда системный frontmatter, security-поля не подделать
            content = _sanitize_and_attach_frontmatter(content, path, key, content_hash, self.on_event)

            # Write (P1-4 fsync внутри atomic_write)
            try:
                atomic_write(out_path, content)
            except OSError as e:
                if isinstance(e, OSError) and e.errno == errno.ENOSPC:  # P2
                    emit_event(IngestEvent(level=IngestEventLevel.RED, type=IngestEventType.DISK_FULL,
                               message=f"Диск полон при записи {out_path}", payload={"path": str(out_path)}), self.on_event)
                logger.exception(f"Write failed for {out_path}")
                return IngestResult(source_path=path_str, output_path="", file_type=suffix,
                                    status="failed", error=f"Write failed: {e}", duration=round(time.time()-start,2))

            # P1-1: потокобезопасная запись
            with self._hashes_lock:
                self.hashes[key] = file_hash
                self._hashes_dirty = True

            emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.FILE_INGESTED,
                       message=f"Ingested {path.name} → {out_path.relative_to(self.vault_dir)}",
                       payload={"source": path_str, "output": str(out_path), "chars": len(content)}), self.on_event)

            # P0-3 + P1/P2: invalidate только при перезаписи (на первом ingest entries нет)
            if was_rewrite:
                self._dispatch_note_changed(str(out_path))

            return IngestResult(source_path=path_str, output_path=str(out_path), file_type=suffix,
                                status="success", chars=len(content), duration=round(time.time()-start,2))
        except Exception as e:
            # P0-4: любой непредвиденный сбой (PermissionError, битый PDF, и т.д.)
            logger.exception(f"Ingest failed for {path}")
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.FILE_FAILED,
                       message=f"Ingest failed: {path.name}: {e}", payload={"path": path_str}), self.on_event)
            return IngestResult(source_path=path_str, output_path="", file_type=suffix,
                                status="failed", error=str(e), duration=round(time.time()-start,2))

    async def ingest_file(self, path: Path) -> IngestResult:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        result = await loop.run_in_executor(None, self._ingest_file_sync, path)
        if self._hashes_dirty:
            await asyncio.to_thread(self._save_hashes_now)  # P0/P1: не блокируем loop
        return result

    async def ingest_directory(self, directory: Path, recursive: bool = True,
                               max_concurrent: Optional[int] = None) -> IngestReport:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if not directory.exists():
            emit_event(IngestEvent(level=IngestEventLevel.RED, type=IngestEventType.VAULT_UNAVAILABLE,
                       message=f"Directory not found: {directory}", payload={"path": str(directory)}), self.on_event)
            raise FileNotFoundError(f"Directory not found: {directory}")
        glob_pattern = "**/*" if recursive else "*"
        files = [f for f in directory.glob(glob_pattern)
                 if f.is_file() and f.suffix.lower() in EXTRACTORS
                 and not any(part in SKIP_DIR_PARTS for part in f.parts)]
        logger.info(f"Ingesting {len(files)} files from {directory}")
        start = time.time()

        # P1-5: Semaphore ограничивает одновременные тяжёлые задачи
        sem = asyncio.Semaphore(max_concurrent or self.max_concurrent)

        async def _limited(f: Path) -> IngestResult:
            async with sem:
                return await loop.run_in_executor(None, self._ingest_file_sync, f)

        results: List[IngestResult] = await asyncio.gather(
            *[_limited(f) for f in files], return_exceptions=False)

        if self._hashes_dirty:
            await asyncio.to_thread(self._save_hashes_now)

        report = IngestReport(total=len(results),
            success=sum(1 for r in results if r.status == "success"),
            skipped=sum(1 for r in results if r.status == "skipped"),
            failed=sum(1 for r in results if r.status == "failed"),
            duration=round(time.time()-start,2), results=results)

        if report.fail_rate > self.BATCH_HIGH_FAIL_THRESHOLD and report.total >= 5:
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.BATCH_HIGH_FAIL_RATE,
                       message=f"Batch fail rate {report.fail_rate:.0%} ({report.failed}/{report.total})",
                       payload={"total": report.total, "failed": report.failed}), self.on_event)
        else:
            emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.BATCH_DONE,
                       message=f"Batch done: {report.success} success, {report.skipped} skipped, {report.failed} failed",
                       payload=report.model_dump(exclude={"results"})), self.on_event)
        return report


# ═══════════════════════════════════════════════════════════════════════════
# InboxWatcher — все операции с _pending в event loop (P1-2)
# ═══════════════════════════════════════════════════════════════════════════

class InboxWatcher:
    def __init__(self, ingestion: DocumentIngestion, inbox_dir: Path, debounce_sec: float = 1.0):
        self.ingestion = ingestion
        self.inbox_dir = inbox_dir.resolve()
        self.debounce_sec = debounce_sec
        self._observer = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending: Dict[str, float] = {}  # доступ ТОЛЬКО из event loop

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            raise ImportError("Install watchdog: pip install watchdog")
        self._loop = loop
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    watcher._enqueue(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    watcher._enqueue(event.src_path)

        self._observer = Observer()
        self._observer.schedule(Handler(), str(self.inbox_dir), recursive=True)
        self._observer.start()
        logger.info(f"InboxWatcher started on {self.inbox_dir}")

    def _enqueue(self, path_str: str) -> None:
        # P1-2/P0-3: из потока watchdog только перебрасываем в loop, без доступа к _pending
        if self._loop is not None and not self._loop.

 [...]