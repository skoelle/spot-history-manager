from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ").replace("\r", " ")
    return " ".join(value.split())


@dataclass
class Cell:
    text: str
    html: str = ""

    @property
    def links(self) -> list[str]:
        return re.findall(r'href\s*=\s*"([^"]+)"', self.html, flags=re.I)

    @property
    def srcs(self) -> list[str]:
        return re.findall(r'src\s*=\s*"([^"]+)"', self.html, flags=re.I)


@dataclass
class Table:
    headers: list[str] = field(default_factory=list)
    rows: list[list[Cell]] = field(default_factory=list)

    def dicts(self) -> list[dict[str, Cell]]:
        if not self.headers:
            return []
        return [
            {header: row[index] if index < len(row) else Cell("") for index, header in enumerate(self.headers)}
            for row in self.rows
        ]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self._table_depth = 0
        self._current: Table | None = None
        self._row: list[tuple[str, Cell]] | None = None
        self._cell_tag: str | None = None
        self._cell_text: list[str] = []
        self._cell_html: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current = Table()
            return
        if self._table_depth != 1 or self._current is None:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell_tag = tag
            self._cell_text = []
            self._cell_html = []
        elif self._cell_tag is not None:
            raw = self.get_starttag_text() or f"<{tag}>"
            self._cell_html.append(raw)
            if tag == "br":
                self._cell_text.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self._table_depth == 1 and self._cell_tag is not None:
            raw = self.get_starttag_text()
            if raw is None:
                attributes = " ".join(f'{key}="{value or ""}"' for key, value in attrs)
                raw = f"<{tag} {attributes}/>" if attributes else f"<{tag}/>"
            self._cell_html.append(raw)

    def handle_data(self, data: str) -> None:
        if self._cell_tag is not None:
            self._cell_text.append(data)
            self._cell_html.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            if self._table_depth == 1 and self._current is not None:
                self.tables.append(self._current)
                self._current = None
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1 or self._current is None:
            return
        if tag in ("td", "th") and self._cell_tag is not None and self._row is not None:
            cell = Cell(
                text=normalize_text("".join(self._cell_text)),
                html="".join(self._cell_html).strip(),
            )
            self._row.append((self._cell_tag, cell))
            self._cell_tag = None
            self._cell_text = []
            self._cell_html = []
        elif tag == "tr" and self._row is not None:
            self._finish_row()
        elif self._cell_tag is not None:
            self._cell_html.append(f"</{tag}>")

    def _finish_row(self) -> None:
        assert self._current is not None and self._row is not None
        if self._row and all(tag == "th" for tag, _ in self._row):
            if not self._current.headers:
                self._current.headers = [cell.text for _, cell in self._row]
        elif self._row:
            self._current.rows.append([cell for _, cell in self._row])
        self._row = None


def read_tables(path: str | Path) -> list[Table]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    return parser.tables


def select_table(tables: list[Table], required: tuple[str, ...], source: str = "") -> Table:
    for table in tables:
        if list(table.headers) == list(required):
            return table
    suffix = f" in {source}" if source else ""
    raise ValueError(f"Tabelle mit Spalten {required!r} nicht gefunden{suffix}")
