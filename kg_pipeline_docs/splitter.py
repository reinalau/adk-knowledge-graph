"""Section-aware chunking for long plain-text documents (e.g. papers exported to .txt)."""
import os
import re
from dataclasses import dataclass

# In testing on a 1,800-word paper introduction, 150-word chunks gave only ~24% more relations
# than 350-word chunks with 2.5x the model calls, so 350 is the default.
MAX_WORDS_PER_CHUNK = int(os.getenv("DOCS_MAX_WORDS_PER_CHUNK", "350"))
MAX_CHUNKS = int(os.getenv("DOCS_MAX_CHUNKS", "20"))  # ~7,000 words with the default chunk size

# Sections that rarely contain domain facts and inflate cost.
SKIPPED_SECTIONS = ("references", "bibliography", "acknowledgments", "acknowledgements")

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_NUMBERED = re.compile(r"^\d+(\.\d+)*\.?\s+")


@dataclass
class Chunk:
    section: str
    text: str

    def labeled(self) -> str:
        return f"Section: {self.section}\n\n{self.text}"


def is_heading(line: str) -> bool:
    """Heuristic: short line, starts with an uppercase letter or a section number, no final punctuation."""
    line = line.strip()
    if not line or len(line.split()) > 10 or line[-1] in ".,;:?!":
        return False
    return bool(_NUMBERED.match(line)) or line[0].isupper()


def _section_name(heading: str) -> str:
    return _NUMBERED.sub("", heading.strip())


def split_sections(text: str) -> list[tuple[str, str]]:
    """Return (section, body) pairs. A heading must be a standalone line followed by a body."""
    sections: list[tuple[str, list[str]]] = [("Preamble", [])]
    for line in text.splitlines():
        if is_heading(line):
            sections.append((_section_name(line), []))
        else:
            sections[-1][1].append(line)
    result = [(name, "\n".join(body).strip()) for name, body in sections]
    return [(name, body) for name, body in result if body]


def _pack(body: str, max_words: int) -> list[str]:
    """Group paragraphs (or sentences of long paragraphs) into pieces of at most `max_words` words."""
    units: list[str] = []
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", body)):
        if not paragraph:
            continue
        paragraph = " ".join(paragraph.split())
        units.extend(_SENTENCE_END.split(paragraph) if len(paragraph.split()) > max_words else [paragraph])

    pieces, current, count = [], [], 0
    for unit in units:
        words = len(unit.split())
        if current and count + words > max_words:
            pieces.append(" ".join(current))
            current, count = [], 0
        current.append(unit)
        count += words
    if current:
        pieces.append(" ".join(current))
    return pieces


def split_document(text: str, max_words: int = MAX_WORDS_PER_CHUNK, max_chunks: int = MAX_CHUNKS) -> tuple[list[Chunk], int]:
    """Split a document into chunks. Returns (chunks, skipped_chunks) where skipped is over the cap."""
    chunks = [
        Chunk(section, piece)
        for section, body in split_sections(text)
        if not section.lower().startswith(SKIPPED_SECTIONS)
        for piece in _pack(body, max_words)
    ]
    return chunks[:max_chunks], max(len(chunks) - max_chunks, 0)
