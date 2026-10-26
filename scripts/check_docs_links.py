"""Validate repository-relative documentation links without network access.

Sphinx validates its own doc/ref roles during the HTML build. This check also
covers Markdown outside the Sphinx tree, archived records, images, and anchors.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^)\n]+)\)")
REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)", re.MULTILINE)
RST_LINK = re.compile(r"<([^<>\n]+)>`_+")
RST_ASSET = re.compile(r"^\s*\.\. (?:image|figure|include|literalinclude)::\s+(\S+)", re.MULTILINE)


@dataclass(frozen=True)
class Link:
    source: Path
    line: int
    target: str


def get_documents(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        root / name
        for name in set(result.stdout.split("\0"))
        if Path(name).suffix in {".md", ".rst"} and (root / name).is_file()
    )


def remove_code_blocks(content: str) -> str:
    # Preserve line positions for diagnostics, including longer fenced examples.
    return re.sub(
        r"(?m)^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$",
        lambda match: "\n" * match.group().count("\n"),
        content,
        flags=re.DOTALL,
    )


def get_links(path: Path) -> list[Link]:
    content = remove_code_blocks(path.read_text())
    patterns = (MARKDOWN_LINK, REFERENCE) if path.suffix == ".md" else (RST_LINK, RST_ASSET)
    links = []
    for pattern in patterns:
        for match in pattern.finditer(content):
            target = match.group(1).strip()
            if target.startswith("<"):
                target = target[1 : target.index(">")]
            else:
                target = re.split(r'\s+["\']', target, maxsplit=1)[0]
            links.append(Link(path, content.count("\n", 0, match.start()) + 1, target))
    return links


def get_anchors(path: Path) -> set[str]:
    content = remove_code_blocks(path.read_text())
    anchors = set(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', content))
    counts: dict[str, int] = {}
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", content, re.MULTILINE):
        title = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", title)
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        ordinal = counts.get(slug, 0)
        anchors.add(f"{slug}-{ordinal}" if ordinal else slug)
        counts[slug] = ordinal + 1
    return anchors


def get_link_error(link: Link, root: Path) -> str | None:
    url = urlsplit(link.target)
    if url.scheme or url.netloc:
        return None
    target = (link.source.parent / unquote(url.path)).resolve() if url.path else link.source
    if not target.is_relative_to(root):
        return "target leaves the repository"
    if not target.exists():
        return "target does not exist"
    if url.fragment and target.suffix == ".md" and unquote(url.fragment) not in get_anchors(target):
        return f"unknown heading #{url.fragment}"
    return None


def main() -> int:
    documents = get_documents(ROOT)
    errors = []
    links = [link for path in documents for link in get_links(path)]
    for link in links:
        if error := get_link_error(link, ROOT):
            errors.append(f"{link.source.relative_to(ROOT)}:{link.line}: {link.target}: {error}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Checked {len(links)} links in {len(documents)} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
