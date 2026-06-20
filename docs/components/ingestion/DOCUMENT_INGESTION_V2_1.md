


---
tags: [крепость, код, ingestion, document]
date: 2026-15
status: готово
version: 2.1
depends_on: smart_cache.py (для invalidate_by_note callback), monitoring.py (для on_event)
---

&quot;&quot;&quot;
krepost/ingestion/document_ingestion.py
Document Ingestion v2.1 — под архитектуру «Крепость».

Конвертирует документы (pdf/docx/txt/md/html/epub) → markdown в vault/ingested/.
Все ингестированные файлы получают frontmatter с `source: external` и
`quarantine: true` — сигнал для основного мозга и security-слоя, что данные
пришли извне и могут содержать prompt injection.

Изменения v2.0 → v2.1 (свод 4 аудитов + прогон кода):
  P0-1  frontmatter injection: пользовательский frontmatter с source:internal/
        quarantine:false ПОЛНОСТЬЮ обходил quarantine. Теперь security-поля
        (source, quarantine, ingested, content_sha256) ВСЕГДА перезаписываются
        через _sanitize_frontmatter — атакующий не может их подделать.
  P0-2  коллизия имён вне base_dir: два разных файла с одинаковым именем падали
        в один vault/ingested/&lt;имя&gt;.md (перезатир + общий хеш-ключ). Теперь для
        файлов вне base_dir в ключ и имя добавляется хеш абсолютного пути.
  P0-3  on_note_changed sync/async контракт: вызов async-callback из потока
        молча терял корутину. Теперь _dispatch_note_changed проверяет
        iscoroutinefunction и гоняет async через run_coroutine_threadsafe.
  P0-4  общий try в _ingest_file_sync: streaming_hash/stat до extract-блока не
        были обёрнуты — PermissionError/FileNotFoundError (файл удалён между
        glob и hash) ронял весь gather. Теперь всё тело под try.
  P1-1  thread-safety hashes: self.hashes пишется из ThreadPoolExecutor —
        защищено threading.Lock (asyncio.Lock потоки не покрывает).
  P1-2  _pending race в watcher: работа с _pending перенесена в event loop через
        call_soon_threadsafe (убирает кросс-потоковый доступ).
  P1-3  DOCX заголовки только по-английски (&quot;Heading N&quot;): русский &quot;Заголовок 1&quot;
        терялся. Теперь по builtin style_id, не по локализованному имени.
  P1-4  fsync перед rename в atomic_write (защита от пустого файла при power-loss).
  P1-5  Semaphore в ingest_directory (защита от OOM при массовом batch).
  P1-6  мёртвые события FILE_SKIPPED/FILE_FAILED теперь эмитятся.
  P2    буфер хеша 64KB, errno.ENOSPC, ingestion_date отдельно, encoding-fallback
        телеметрия, init_logging без дубля sink, DOCX \\n в ячейках → &lt;br&gt;.

Концепт «Учитель» удалён из проекта — в комментариях/контрактах его нет.
&quot;&quot;&quot;

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


def init_logging(log_dir: Path = Path(&quot;data/logs&quot;)) -&gt; None:
    # P2: защита от дубля sink при повторном вызове
    global _LOG_SINK_ID
    log_dir.mkdir(parents=True, exist_ok=True)
    if _LOG_SINK_ID is not None:
        try:
            logger.remove(_LOG_SINK_ID)
        except ValueError:
            pass
    _LOG_SINK_ID = logger.add(log_dir / &quot;ingestion.log&quot;, rotation=&quot;10 MB&quot;,
                              level=&quot;INFO&quot;, enqueue=True)


# ═══════════════════════════════════════════════════════════════════════════
# События
# ═══════════════════════════════════════════════════════════════════════════

class IngestEventLevel(str, Enum):
    GREEN = &quot;green&quot;
    YELLOW = &quot;yellow&quot;
    RED = &quot;red&quot;


class IngestEventType(str, Enum):
    FILE_INGESTED = &quot;file_ingested&quot;
    FILE_SKIPPED = &quot;file_skipped&quot;
    FILE_FAILED = &quot;file_failed&quot;
    BATCH_DONE = &quot;batch_done&quot;
    BATCH_HIGH_FAIL_RATE = &quot;batch_high_fail_rate&quot;
    LARGE_FILE_DETECTED = &quot;large_file_detected&quot;
    VAULT_UNAVAILABLE = &quot;vault_unavailable&quot;
    DISK_FULL = &quot;disk_full&quot;
    OCR_FALLBACK_USED = &quot;ocr_fallback_used&quot;
    ENCODING_FALLBACK = &quot;encoding_fallback&quot;
    FRONTMATTER_OVERRIDDEN = &quot;frontmatter_overridden&quot;


@dataclass
class IngestEvent:
    level: IngestEventLevel
    type: IngestEventType
    message: str
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


def emit_event(event: IngestEvent, callback: Optional[Callable[[IngestEvent], None]]) -&gt; None:
    &quot;&quot;&quot;
    Локальные логи + внешний callback. ВАЖНО: callback вызывается синхронно,
    в т.ч. из потока executor — он ДОЛЖЕН быть sync и потокобезопасным
    (для async-доставки в Telegram оборачивай на стороне monitoring.py).
    &quot;&quot;&quot;
    msg = f&quot;[{event.level.value.upper()}] {event.type.value}: {event.message}&quot;
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
            logger.exception(&quot;on_event callback failed&quot;)


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic-модели
# ═══════════════════════════════════════════════════════════════════════════

IngestStatus = Literal[&quot;success&quot;, &quot;skipped&quot;, &quot;failed&quot;]


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
    def fail_rate(self) -&gt; float:
        return self.failed / self.total if self.total else 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Утилиты
# ═══════════════════════════════════════════════════════════════════════════

SKIP_DIR_PARTS = {&quot;.git&quot;, &quot;.obsidian&quot;, &quot;node_modules&quot;, &quot;__pycache__&quot;, &quot;.venv&quot;, &quot;.idea&quot;, &quot;.DS_Store&quot;}

# Security-поля frontmatter, которые НИКОГДА не берутся из пользовательского
# документа — всегда задаются системой (P0-1).
SECURITY_FM_FIELDS = {&quot;source&quot;, &quot;quarantine&quot;, &quot;ingested&quot;, &quot;content_sha256&quot;, &quot;source_path&quot;}


def streaming_hash(path: Path, chunk_size: int = 65536) -&gt; str:
    &quot;&quot;&quot;SHA-256 без загрузки файла целиком. Буфер 64KB (P2: быстрее на NVMe).&quot;&quot;&quot;
    h = hashlib.sha256()
    with open(path, &quot;rb&quot;) as f:
        for chunk in iter(lambda: f.read(chunk_size), b&quot;&quot;):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, content: str) -&gt; None:
    &quot;&quot;&quot;Запись через temp + fsync + atomic rename (P1-4: защита от пустого файла при power-loss).&quot;&quot;&quot;
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + &quot;.tmp&quot;)
    with open(tmp, &quot;w&quot;, encoding=&quot;utf-8&quot;) as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def detect_and_read_text(path: Path,
                         on_event: Optional[Callable[[IngestEvent], None]] = None) -&gt; str:
    raw = path.read_bytes()
    for enc in (&quot;utf-8-sig&quot;, &quot;utf-8&quot;, &quot;windows-1251&quot;, &quot;cp1252&quot;):
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
                           message=f&quot;Decode с заменой символов: {path.name}&quot;,
                           payload={&quot;path&quot;: str(path)}), on_event)
    return raw.decode(&quot;utf-8&quot;, errors=&quot;replace&quot;)


def _parse_existing_frontmatter(content: str) -&gt; tuple[dict, str]:
    &quot;&quot;&quot;
    Строгий парс frontmatter (P0-1, P2): только если контент начинается с &#x27;---\\n&#x27;
    И есть закрывающий &#x27;---&#x27;. Возвращает (поля, тело_без_frontmatter).
    Если frontmatter нет/битый — ({}, исходный_контент).
    &quot;&quot;&quot;
    stripped = content.lstrip(&quot;\ufeff&quot;)  # снять BOM
    if not stripped.startswith(&quot;---&quot;):
        return {}, content
    # ищем закрывающий разделитель
    m = re.match(r&quot;^---\s*\n(.*?)\n---\s*\n?(.*)$&quot;, stripped, re.DOTALL)
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
                      content_hash: str, existing: Optional[dict] = None) -&gt; str:
    &quot;&quot;&quot;
    Собирает frontmatter. Security-поля ВСЕГДА системные (P0-1): даже если в
    existing пришли source:internal/quarantine:false — они перезаписываются.
    Несекьюрные поля пользователя (если были) сохраняются.
    &quot;&quot;&quot;
    existing = existing or {}

    title = source_path.stem.replace(&quot;-&quot;, &quot; &quot;).replace(&quot;_&quot;, &quot; &quot;).title()
    h1 = re.search(r&#x27;^#\s+(.+)&#x27;, content_body, re.MULTILINE)
    if h1:
        title = h1.group(1).strip()
    if isinstance(existing.get(&quot;title&quot;), str) and existing[&quot;title&quot;].strip():
        title = existing[&quot;title&quot;].strip()

    # P2: теги — буквы И цифры (rfc2119 больше не теряется)
    tags = [w.lower() for w in re.findall(r&#x27;\b[\wа-яА-Я]{4,}\b&#x27;, source_path.stem)][:5]
    if isinstance(existing.get(&quot;tags&quot;), list):
        tags = [str(t) for t in existing[&quot;tags&quot;]][:10] or tags

    # сохраняем безопасные пользовательские поля, выкидывая security-критичные
    safe_user = {k: v for k, v in existing.items()
                 if k not in SECURITY_FM_FIELDS and k not in (&quot;title&quot;, &quot;tags&quot;, &quot;date&quot;, &quot;ingestion_date&quot;)}

    fm_dict = {
        &quot;title&quot;: title,
        &quot;tags&quot;: tags or [&quot;ingested&quot;],
        # P2: doc-date сохраняется при re-ingest, ingestion_date обновляется
        &quot;date&quot;: existing.get(&quot;date&quot;) or datetime.now().strftime(&quot;%Y-%m-%d&quot;),
        &quot;ingestion_date&quot;: datetime.now().strftime(&quot;%Y-%m-%d&quot;),
        # --- SECURITY-поля, всегда системные ---
        &quot;source&quot;: &quot;external&quot;,
        &quot;source_format&quot;: source_path.suffix.lstrip(&quot;.&quot;),
        &quot;source_path&quot;: str(relative_path),
        &quot;content_sha256&quot;: content_hash[:16],
        &quot;ingested&quot;: True,
        &quot;quarantine&quot;: True,
    }
    fm_dict.update(safe_user)  # безопасные поля не могут переопределить security
    for k in SECURITY_FM_FIELDS:
        if k == &quot;source&quot;:
            fm_dict[k] = &quot;external&quot;
        elif k == &quot;quarantine&quot;:
            fm_dict[k] = True
        elif k == &quot;ingested&quot;:
            fm_dict[k] = True
    yaml_block = yaml.safe_dump(fm_dict, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f&quot;---\n{yaml_block}---\n\n&quot;


def _sanitize_and_attach_frontmatter(content: str, source_path: Path, relative_path: str,
                                     content_hash: str,
                                     on_event: Optional[Callable[[IngestEvent], None]] = None) -&gt; str:
    &quot;&quot;&quot;
    P0-1: единая точка — всегда выдаёт документ с системным frontmatter.
    Пользовательский frontmatter парсится, но security-поля перезаписываются.
    &quot;&quot;&quot;
    existing, body = _parse_existing_frontmatter(content)
    if existing:
        # были попытки задать security-поля вручную? — сигнал
        if any(k in existing for k in SECURITY_FM_FIELDS):
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW,
                                   type=IngestEventType.FRONTMATTER_OVERRIDDEN,
                                   message=f&quot;Перезаписаны security-поля frontmatter в {source_path.name}&quot;,
                                   payload={&quot;path&quot;: str(source_path)}), on_event)
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
        if child.tag == qn(&quot;w:p&quot;):
            yield (&quot;paragraph&quot;, Paragraph(child, doc))
        elif child.tag == qn(&quot;w:tbl&quot;):
            yield (&quot;table&quot;, Table(child, doc))


def _docx_heading_level(paragraph) -&gt; Optional[int]:
    &quot;&quot;&quot;
    P1-3: уровень заголовка по builtin style_id (не по локализованному имени).
    Встроенные стили Word имеют style_id &#x27;Heading1&#x27;..&#x27;Heading9&#x27; независимо
    от языка интерфейса (&#x27;Заголовок 1&#x27;, &#x27;Titre 1&#x27; и т.д.).
    &quot;&quot;&quot;
    style = paragraph.style
    if style is None:
        return None
    sid = getattr(style, &quot;style_id&quot;, &quot;&quot;) or &quot;&quot;
    m = re.match(r&quot;Heading(\d+)&quot;, sid)
    if m:
        return min(int(m.group(1)), 6)
    # запасной путь — английское имя
    name = style.name or &quot;&quot;
    m2 = re.match(r&quot;Heading (\d+)&quot;, name)
    if m2:
        return min(int(m2.group(1)), 6)
    return None


def extract_pdf(path: Path, enable_ocr: bool = False,
                on_event: Optional[Callable[[IngestEvent], None]] = None) -&gt; str:
    import fitz
    pages_text: List[str] = []
    has_empty_pages = False
    with fitz.open(str(path)) as doc:
        n_pages = len(doc)
        if n_pages &gt; MAX_PDF_PAGES:
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.LARGE_FILE_DETECTED,
                       message=f&quot;PDF {path.name}: {n_pages} страниц &gt; лимит {MAX_PDF_PAGES}, обрезано&quot;,
                       payload={&quot;path&quot;: str(path), &quot;pages&quot;: n_pages}), on_event)
        for i, page in enumerate(doc):
            if i &gt;= MAX_PDF_PAGES:
                break
            text = page.get_text(&quot;text&quot;).strip()
            if not text and enable_ocr:
                text = _ocr_pdf_page(page)
                if text:
                    emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.OCR_FALLBACK_USED,
                               message=f&quot;OCR использован для {path.name} стр.{i+1}&quot;,
                               payload={&quot;path&quot;: str(path), &quot;page&quot;: i + 1}), on_event)
            if text:
                pages_text.append(f&quot;&lt;!-- Page {i+1} --&gt;\n{text}&quot;)
            else:
                has_empty_pages = True
    if has_empty_pages and not enable_ocr:
        logger.warning(f&quot;{path.name}: пустые страницы, OCR выключен&quot;)
    final = &quot;\n\n&quot;.join(pages_text)
    if len(final) &gt; MAX_CONTENT_CHARS:
        final = final[:MAX_CONTENT_CHARS] + &quot;\n\n[truncated]&quot;
    return final


def _ocr_pdf_page(page) -&gt; str:
    try:
        import pytesseract
        from PIL import Image
        import io
        pix = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes(&quot;png&quot;)))
        return pytesseract.image_to_string(img, lang=&quot;rus+eng&quot;).strip()
    except ImportError:
        logger.debug(&quot;pytesseract not installed — OCR disabled&quot;)
        return &quot;&quot;
    except Exception as e:
        # P2: TesseractNotFoundError → явный лог про бинарь
        if e.__class__.__name__ == &quot;TesseractNotFoundError&quot;:
            logger.error(&quot;Tesseract binary не найден в системе (brew install tesseract)&quot;)
        else:
            logger.exception(&quot;OCR failed for page&quot;)
        return &quot;&quot;


def extract_docx(path: Path) -&gt; str:
    from docx import Document
    doc = Document(str(path))
    parts: List[str] = []
    for block_type, block in _iter_docx_blocks(doc):
        if block_type == &quot;paragraph&quot;:
            text = block.text.strip()
            if not text:
                continue
            level = _docx_heading_level(block)   # P1-3
            style = block.style.name if block.style else &quot;&quot;
            if level is not None:
                parts.append(f&quot;{&#x27;#&#x27; * level} {text}&quot;)
            elif &quot;List&quot; in style:
                parts.append(f&quot;- {text}&quot;)
            else:
                parts.append(text)
        elif block_type == &quot;table&quot;:
            rows = []
            for i, row in enumerate(block.rows):
                # P2: \n в ячейке ломает markdown-таблицу → &lt;br&gt;
                cells = [c.text.strip().replace(&quot;|&quot;, &quot;\\|&quot;).replace(&quot;\n&quot;, &quot;&lt;br&gt;&quot;) for c in row.cells]
                rows.append(&quot;| &quot; + &quot; | &quot;.join(cells) + &quot; |&quot;)
                if i == 0:
                    rows.append(&quot;|&quot; + &quot;---|&quot; * len(cells))
            if rows:
                parts.append(&quot;\n&quot;.join(rows))
    return &quot;\n\n&quot;.join(parts)


def extract_txt(path: Path) -&gt; str:
    return detect_and_read_text(path)


def extract_md(path: Path) -&gt; str:
    return detect_and_read_text(path)


def extract_html(path: Path) -&gt; str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError(&quot;HTML support requires: pip install beautifulsoup4&quot;)
    html = detect_and_read_text(path)
    soup = BeautifulSoup(html, &quot;html.parser&quot;)
    for tag in soup([&quot;script&quot;, &quot;style&quot;, &quot;nav&quot;, &quot;footer&quot;]):
        tag.decompose()
    return soup.get_text(separator=&quot;\n&quot;).strip()


def extract_epub(path: Path) -&gt; str:
    try:
        import ebooklib
        from ebooklib import epub
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError(&quot;EPUB support requires: pip install ebooklib beautifulsoup4&quot;)
    book = epub.read_epub(str(path))
    parts: List[str] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), &quot;html.parser&quot;)
        text = soup.get_text(separator=&quot;\n&quot;).strip()
        if text:
            parts.append(text)
    return &quot;\n\n&quot;.join(parts)


EXTRACTORS = {
    &quot;.pdf&quot;: extract_pdf, &quot;.docx&quot;: extract_docx, &quot;.txt&quot;: extract_txt,
    &quot;.md&quot;: extract_md, &quot;.html&quot;: extract_html, &quot;.htm&quot;: extract_html, &quot;.epub&quot;: extract_epub,
}


# ═══════════════════════════════════════════════════════════════════════════
# DocumentIngestion
# ═══════════════════════════════════════════════════════════════════════════

class DocumentIngestion:
    DEFAULT_BASE_DIR = Path(&quot;inbox&quot;)
    DEFAULT_VAULT_DIR = Path(&quot;vault&quot;)
    INGESTED_SUBDIR = &quot;ingested&quot;
    MAX_FILE_SIZE_MB = 100          # P0-2-related: понижен с 500
    BATCH_HIGH_FAIL_THRESHOLD = 0.5
    DEFAULT_MAX_CONCURRENT = 4      # P1-5

    def __init__(self, base_dir=DEFAULT_BASE_DIR, vault_dir=DEFAULT_VAULT_DIR,
                 hashes_path=Path(&quot;data/ingestion_hashes.json&quot;), max_file_size_mb=MAX_FILE_SIZE_MB,
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

    def _load_hashes(self) -&gt; Dict[str, str]:
        if not self.hashes_path.exists():
            return {}
        try:
            return json.loads(self.hashes_path.read_text(encoding=&quot;utf-8&quot;))
        except Exception:
            logger.exception(&quot;Failed to load hashes — starting fresh&quot;)
            return {}

    def _save_hashes_now(self) -&gt; None:
        try:
            with self._hashes_lock:
                snapshot = dict(self.hashes)
                self._hashes_dirty = False
            atomic_write(self.hashes_path, json.dumps(snapshot, indent=2, ensure_ascii=False))
        except Exception:
            logger.exception(&quot;Failed to save hashes&quot;)

    # ── Paths (P0-2) ─────────────────────────────────────────────────────────

    def _path_tag(self, source: Path) -&gt; str:
        &quot;&quot;&quot;Короткий хеш абсолютного пути — разводит одноимённые файлы вне base_dir.&quot;&quot;&quot;
        return hashlib.sha256(str(source.resolve()).encode(&quot;utf-8&quot;)).hexdigest()[:8]

    def _relative_key(self, source: Path) -&gt; str:
        try:
            return str(source.resolve().relative_to(self.base_dir))
        except ValueError:
            # P0-2: вне base_dir — имя + хеш пути, иначе коллизия ключей
            return f&quot;{source.stem}__{self._path_tag(source)}{source.suffix}&quot;

    def _output_path(self, source: Path) -&gt; Path:
        try:
            rel = source.resolve().relative_to(self.base_dir).with_suffix(&quot;.md&quot;)
        except ValueError:
            # P0-2: вне base_dir — имя + хеш пути, иначе перезатир в vault
            rel = Path(f&quot;{source.stem}__{self._path_tag(source)}.md&quot;)
        return self.ingested_root / rel

    # ── on_note_changed dispatch (P0-3) ──────────────────────────────────────

    def _dispatch_note_changed(self, out_path_str: str) -&gt; None:
        cb = self.on_note_changed
        if cb is None:
            return
        try:
            if asyncio.iscoroutinefunction(cb):
                if self._loop is not None and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(cb(out_path_str), self._loop)
                else:
                    logger.warning(&quot;async on_note_changed, но loop недоступен — пропуск&quot;)
            else:
                cb(out_path_str)
        except Exception:
            logger.exception(&quot;on_note_changed callback failed&quot;)

    # ── Core ──────────────────────────────────────────────────────────────────

    def _ingest_file_sync(self, path: Path) -&gt; IngestResult:
        start = time.time()
        suffix = path.suffix.lower()
        path_str = str(path)
        # P0-4: всё тело под try — один битый файл не рушит batch
        try:
            if not path.exists():
                emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.FILE_FAILED,
                           message=f&quot;File not found: {path.name}&quot;, payload={&quot;path&quot;: path_str}), self.on_event)
                return IngestResult(source_path=path_str, output_path=&quot;&quot;, file_type=suffix,
                                    status=&quot;failed&quot;, error=&quot;File not found&quot;, duration=round(time.time()-start,2))
            if suffix not in EXTRACTORS:
                emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.FILE_SKIPPED,
                           message=f&quot;Unsupported format: {suffix}&quot;, payload={&quot;path&quot;: path_str}), self.on_event)
                return IngestResult(source_path=path_str, output_path=&quot;&quot;, file_type=suffix,
                                    status=&quot;failed&quot;, error=f&quot;Unsupported format: {suffix}&quot;, duration=round(time.time()-start,2))

            size_mb = path.stat().st_size / (1024 * 1024)
            if size_mb &gt; self.max_file_size_mb:
                emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.LARGE_FILE_DETECTED,
                           message=f&quot;Файл {path.name} {size_mb:.0f} МБ &gt; лимит {self.max_file_size_mb} МБ&quot;,
                           payload={&quot;path&quot;: path_str, &quot;size_mb&quot;: size_mb}), self.on_event)
                return IngestResult(source_path=path_str, output_path=&quot;&quot;, file_type=suffix,
                                    status=&quot;failed&quot;, error=f&quot;File too large: {size_mb:.0f} MB&quot;, duration=round(time.time()-start,2))

            file_hash = streaming_hash(path)
            key = self._relative_key(path)
            out_path = self._output_path(path)

            # P1-1: потокобезопасное чтение
            with self._hashes_lock:
                already = (self.hashes.get(key) == file_hash)
            if already and out_path.exists():
                emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.FILE_SKIPPED,
                           message=f&quot;Skip (unchanged): {path.name}&quot;, payload={&quot;path&quot;: path_str}), self.on_event)
                return IngestResult(source_path=path_str, output_path=str(out_path), file_type=suffix,
                                    status=&quot;skipped&quot;, duration=round(time.time()-start,2))

            # Extract
            try:
                extractor = EXTRACTORS[suffix]
                if suffix == &quot;.pdf&quot;:
                    content = extractor(path, enable_ocr=self.enable_ocr, on_event=self.on_event)
                else:
                    content = extractor(path)
            except ImportError as e:
                return IngestResult(source_path=path_str, output_path=&quot;&quot;, file_type=suffix,
                                    status=&quot;failed&quot;, error=f&quot;Optional lib missing: {e}&quot;, duration=round(time.time()-start,2))

            if not content.strip():
                return IngestResult(source_path=path_str, output_path=&quot;&quot;, file_type=suffix,
                                    status=&quot;failed&quot;, error=&quot;Empty content (OCR disabled?)&quot;, duration=round(time.time()-start,2))

            content_hash = hashlib.sha256(content.encode(&quot;utf-8&quot;)).hexdigest()
            was_rewrite = out_path.exists()   # P1/P2: invalidate только при перезаписи

            # P0-1: всегда системный frontmatter, security-поля не подделать
            content = _sanitize_and_attach_frontmatter(content, path, key, content_hash, self.on_event)

            # Write (P1-4 fsync внутри atomic_write)
            try:
                atomic_write(out_path, content)
            except OSError as e:
                if isinstance(e, OSError) and e.errno == errno.ENOSPC:  # P2
                    emit_event(IngestEvent(level=IngestEventLevel.RED, type=IngestEventType.DISK_FULL,
                               message=f&quot;Диск полон при записи {out_path}&quot;, payload={&quot;path&quot;: str(out_path)}), self.on_event)
                logger.exception(f&quot;Write failed for {out_path}&quot;)
                return IngestResult(source_path=path_str, output_path=&quot;&quot;, file_type=suffix,
                                    status=&quot;failed&quot;, error=f&quot;Write failed: {e}&quot;, duration=round(time.time()-start,2))

            # P1-1: потокобезопасная запись
            with self._hashes_lock:
                self.hashes[key] = file_hash
                self._hashes_dirty = True

            emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.FILE_INGESTED,
                       message=f&quot;Ingested {path.name} → {out_path.relative_to(self.vault_dir)}&quot;,
                       payload={&quot;source&quot;: path_str, &quot;output&quot;: str(out_path), &quot;chars&quot;: len(content)}), self.on_event)

            # P0-3 + P1/P2: invalidate только при перезаписи (на первом ingest entries нет)
            if was_rewrite:
                self._dispatch_note_changed(str(out_path))

            return IngestResult(source_path=path_str, output_path=str(out_path), file_type=suffix,
                                status=&quot;success&quot;, chars=len(content), duration=round(time.time()-start,2))
        except Exception as e:
            # P0-4: любой непредвиденный сбой (PermissionError, битый PDF, и т.д.)
            logger.exception(f&quot;Ingest failed for {path}&quot;)
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.FILE_FAILED,
                       message=f&quot;Ingest failed: {path.name}: {e}&quot;, payload={&quot;path&quot;: path_str}), self.on_event)
            return IngestResult(source_path=path_str, output_path=&quot;&quot;, file_type=suffix,
                                status=&quot;failed&quot;, error=str(e), duration=round(time.time()-start,2))

    async def ingest_file(self, path: Path) -&gt; IngestResult:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        result = await loop.run_in_executor(None, self._ingest_file_sync, path)
        if self._hashes_dirty:
            await asyncio.to_thread(self._save_hashes_now)  # P0/P1: не блокируем loop
        return result

    async def ingest_directory(self, directory: Path, recursive: bool = True,
                               max_concurrent: Optional[int] = None) -&gt; IngestReport:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if not directory.exists():
            emit_event(IngestEvent(level=IngestEventLevel.RED, type=IngestEventType.VAULT_UNAVAILABLE,
                       message=f&quot;Directory not found: {directory}&quot;, payload={&quot;path&quot;: str(directory)}), self.on_event)
            raise FileNotFoundError(f&quot;Directory not found: {directory}&quot;)
        glob_pattern = &quot;**/*&quot; if recursive else &quot;*&quot;
        files = [f for f in directory.glob(glob_pattern)
                 if f.is_file() and f.suffix.lower() in EXTRACTORS
                 and not any(part in SKIP_DIR_PARTS for part in f.parts)]
        logger.info(f&quot;Ingesting {len(files)} files from {directory}&quot;)
        start = time.time()

        # P1-5: Semaphore ограничивает одновременные тяжёлые задачи
        sem = asyncio.Semaphore(max_concurrent or self.max_concurrent)

        async def _limited(f: Path) -&gt; IngestResult:
            async with sem:
                return await loop.run_in_executor(None, self._ingest_file_sync, f)

        results: List[IngestResult] = await asyncio.gather(
            *[_limited(f) for f in files], return_exceptions=False)

        if self._hashes_dirty:
            await asyncio.to_thread(self._save_hashes_now)

        report = IngestReport(total=len(results),
            success=sum(1 for r in results if r.status == &quot;success&quot;),
            skipped=sum(1 for r in results if r.status == &quot;skipped&quot;),
            failed=sum(1 for r in results if r.status == &quot;failed&quot;),
            duration=round(time.time()-start,2), results=results)

        if report.fail_rate &gt; self.BATCH_HIGH_FAIL_THRESHOLD and report.total &gt;= 5:
            emit_event(IngestEvent(level=IngestEventLevel.YELLOW, type=IngestEventType.BATCH_HIGH_FAIL_RATE,
                       message=f&quot;Batch fail rate {report.fail_rate:.0%} ({report.failed}/{report.total})&quot;,
                       payload={&quot;total&quot;: report.total, &quot;failed&quot;: report.failed}), self.on_event)
        else:
            emit_event(IngestEvent(level=IngestEventLevel.GREEN, type=IngestEventType.BATCH_DONE,
                       message=f&quot;Batch done: {report.success} success, {report.skipped} skipped, {report.failed} failed&quot;,
                       payload=report.model_dump(exclude={&quot;results&quot;})), self.on_event)
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

    def start(self, loop: asyncio.AbstractEventLoop) -&gt; None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            raise ImportError(&quot;Install watchdog: pip install watchdog&quot;)
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
        logger.info(f&quot;InboxWatcher started on {self.inbox_dir}&quot;)

    def _enqueue(self, path_str: str) -&gt; None:
        # P1-2/P0-3: из потока watchdog только перебрасываем в loop, без доступа к _pending
        if self._loop is not None and not self._loop.

 [...]