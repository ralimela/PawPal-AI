"""Retrieval over the knowledge/ folder (the "R" in RAG).

Reused from the DocuBot project:
- Chunking: each Markdown heading starts a new chunk; a '###' keeps its parent '##'.
- Words are lowercased, split on non-letters, and one common ending is stripped,
  so 'feeding' matches 'feed' and 'walks' matches 'walk'.
- Guardrail: a chunk only counts as evidence if it contains at least half of
  the question's meaningful words, and at least 2 of them (min_words_needed).
- Ranking: most DIFFERENT question words matched first, total matches second.
"""

import glob
import math
import os
import re
from dataclasses import dataclass

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "be", "to", "of", "in", "on", "for",
    "and", "or", "it", "this", "that", "with", "as", "by", "at", "do", "does",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "his", "her",
    "him", "they", "their", "them", "its", "how", "what", "where", "which",
    "when", "who", "why", "can", "could", "should", "would", "will", "there",
    "from", "if", "any", "these", "about", "get", "much", "many", "some", "so",
    "help", "tell", "know", "please",
}
SUFFIXES = ["ing", "ion", "ed", "es", "e", "s"]
MIN_QUERY_COVERAGE = 0.5


def normalize_word(word: str) -> str:
    """Strip one common ending if at least 3 letters remain (dogs -> dog, walks -> walk)."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def split_words(text: str) -> list[str]:
    """Lowercase and split on anything that isn't a letter or digit."""
    return re.findall(r"[a-z0-9]+", text.lower())


def tokenize(text: str) -> list[str]:
    """Normalized words of a piece of text."""
    return [normalize_word(w) for w in split_words(text)]


def query_words(query: str) -> set[str]:
    """Meaningful, normalized, de-duplicated words of a question.

    One-letter pieces are dropped: "cat's" splits into "cat" + "s", and that
    stray "s" would otherwise count as a question word.
    """
    return {normalize_word(w) for w in split_words(query)
            if w not in STOPWORDS and len(w) > 1}


def split_into_sections(text: str) -> list[str]:
    """Split Markdown at headings; a '###' chunk keeps its parent '##' heading on top."""
    sections, current, parent = [], [], None
    for line in text.splitlines():
        if line.startswith("#"):
            if current:
                sections.append("\n".join(current).strip())
            current = []
            if line.startswith("## "):
                parent = line
            elif line.startswith("### ") and parent:
                current.append(parent)
        current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [s for s in sections if s]


def min_words_needed(query_size: int) -> int:
    """Guardrail: how many question words a chunk must contain to count as evidence.

    At least half of them (MIN_QUERY_COVERAGE), and at least 2 when the question
    has 2 or more words, so one coincidental word ('make' in 'how do I make
    pasta?') can't pass on its own.
    """
    return max(math.ceil(query_size * MIN_QUERY_COVERAGE), min(2, query_size))


@dataclass
class Chunk:
    """One section of one guide."""

    filename: str
    heading: str   # e.g. "Feeding Cats"
    text: str

    @property
    def label(self) -> str:
        """Citation label shown to the user, e.g. 'cat_care.md > Feeding Cats'."""
        return f"{self.filename} > {self.heading}"


class KnowledgeBase:
    """Loads the guides once, then answers search() calls."""

    def __init__(self, folder: str = "knowledge"):
        """Load every .md file in folder, split it into chunks, and index the words."""
        self.chunks: list[Chunk] = []
        for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for section in split_into_sections(text):
                first_line = section.splitlines()[0]
                if not first_line.startswith("## ") and not first_line.startswith("### "):
                    continue  # skip each file's '# Title' intro; it has no care facts
                heading = first_line.lstrip("#").strip()
                self.chunks.append(Chunk(os.path.basename(path), heading, section))
        # Inverted index: word -> ids of chunks containing it
        self.index: dict[str, set[int]] = {}
        self._chunk_words: list[set[str]] = []
        for chunk_id, chunk in enumerate(self.chunks):
            words = set(tokenize(chunk.text))
            self._chunk_words.append(words)
            for word in words:
                self.index.setdefault(word, set()).add(chunk_id)

    def search(self, query: str, top_k: int = 3) -> list[Chunk]:
        """Return up to top_k chunks that pass the coverage guardrail, best first."""
        words = query_words(query)
        if not words:
            return []
        candidates = set().union(*(self.index.get(w, set()) for w in words))
        ranked = []
        for chunk_id in candidates:
            matched = len(words & self._chunk_words[chunk_id])
            if matched < min_words_needed(len(words)):
                continue  # guardrail: too little of the question is in this chunk
            total = sum(tokenize(self.chunks[chunk_id].text).count(w) for w in words)
            ranked.append((matched, total, chunk_id))
        # Best first; ties go to the chunk that appears earlier in the guides.
        ranked.sort(key=lambda r: (-r[0], -r[1], r[2]))
        return [self.chunks[chunk_id] for _, _, chunk_id in ranked[:top_k]]
