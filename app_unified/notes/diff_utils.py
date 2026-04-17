"""Paragraph-level diff utility for NoteLog."""
import difflib

# Separator used when multiple changed paragraphs are stored in NoteLog.diff
PARAGRAPH_DIFF_SEP = "\n---\n"
# Prefix stored in NoteLog.diff to identify paragraph-level diffs
PARAGRAPH_DIFF_PREFIX = "[段落diff]\n"


def compute_paragraph_diff(old_content, new_content):
    """Compare old and new note content line-by-line, return changed paragraphs (new version).

    Returns:
        str — changed paragraphs joined by PARAGRAPH_DIFF_SEP, or
        None — if no change, pure deletion, or old is empty/None (caller should use full content).
    """
    if not old_content:
        return None

    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n") if new_content else []

    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    changed = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            changed.extend(new_lines[j1:j2])

    if not changed:
        return None

    return PARAGRAPH_DIFF_SEP.join(changed)
