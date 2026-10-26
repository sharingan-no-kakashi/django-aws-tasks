"""Regression checks for documentation links outside Sphinx's source tree."""

from scripts.check_docs_links import Link, get_anchors, get_link_error, get_links


def test_missing_files_and_sibling_checkouts_are_rejected(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("# Readme\n")

    assert get_link_error(Link(source, 1, "missing.md"), tmp_path) == "target does not exist"
    assert get_link_error(Link(source, 1, "../example/README.md"), tmp_path) == (
        "target leaves the repository"
    )


def test_markdown_headings_include_duplicate_and_explicit_anchors(tmp_path):
    source = tmp_path / "guide.md"
    source.write_text('# Task `status`\n# Task status\n<a id="custom"></a>\n')

    assert get_anchors(source) == {"task-status", "task-status-1", "custom"}
    assert get_link_error(Link(source, 1, "#task-status-1"), tmp_path) is None
    assert get_link_error(Link(source, 1, "#removed"), tmp_path) == "unknown heading #removed"


def test_markdown_extracts_images_and_reference_destinations_but_ignores_examples(tmp_path):
    source = tmp_path / "README.md"
    source.write_text(
        '[Guide](guide.md "Title")\n![Design](image.svg)\n'
        "[reference]: <file with spaces.md>\n"
        "```markdown\n[Example](missing.md)\n```\n"
    )

    assert {link.target for link in get_links(source)} == {
        "guide.md",
        "image.svg",
        "file with spaces.md",
    }


def test_rst_links_and_assets_are_checked(tmp_path):
    source = tmp_path / "index.rst"
    source.write_text("`Guide <guide.rst>`_\n\n.. figure:: image.svg\n")

    assert {link.target for link in get_links(source)} == {"guide.rst", "image.svg"}


def test_encoded_local_paths_and_external_links(tmp_path):
    source = tmp_path / "README.md"
    (tmp_path / "file with spaces.md").write_text("# Guide\n")

    assert get_link_error(Link(source, 1, "file%20with%20spaces.md#guide"), tmp_path) is None
    assert get_link_error(Link(source, 1, "https://example.com/guide"), tmp_path) is None
    assert get_link_error(Link(source, 1, "mailto:maintainer@example.com"), tmp_path) is None
