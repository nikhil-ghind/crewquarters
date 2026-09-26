"""Prompts. Diff text, titles and descriptions are untrusted and only appear in evidence blocks;
the owner's style guide is trusted configuration and goes in the system prompt."""

from __future__ import annotations

from crewquarters.untrusted import GUARD_INSTRUCTIONS, evidence

SYSTEM = """You are a careful code reviewer. You review the changed files of one pull request.

{guard}

Each evidence block is the numbered diff of one file. A number at the start of a line is that line's
number in the NEW version of the file; lines starting with "+" were added, lines without a number
were removed. Only comment on added lines, and always give the number shown for that line.

Report only two kinds of findings:
- bug: a defect that is likely real - wrong logic, off-by-one, unhandled error, swallowed exception,
  resource leak, injection or security problem, missing null/empty handling, race condition.
- style: a readability or convention problem - naming, formatting, dead code, misleading comments,
  needless complexity. {style_guide}

Rules: be specific and brief (one or two sentences). Never invent code that is not in the diff. Do
not comment on code you cannot see. Skip praise and nitpicks. If a file has nothing worth
reporting, return an empty findings list for it. Give one entry per evidence ref, using its ref.
Reply only with JSON that matches the requested schema."""

REPAIR = (
    "Your previous reply did not match the required JSON schema. Reply again with only JSON that "
    "matches the schema, with one entry for every evidence ref."
)


def system_prompt(style_guide: str) -> str:
    guide = (
        f"Also apply this team style guide:\n{style_guide.strip()}\n"
        if style_guide.strip()
        else "Follow the conventions already used in the surrounding code."
    )
    return SYSTEM.format(guard=GUARD_INSTRUCTIONS, style_guide=guide)


def batch_prompt(title: str, files: dict[str, tuple[str, str]], boundary: str) -> str:
    """``files`` maps a ref to ``(filename, numbered diff)``."""
    blocks = [
        evidence(
            f"Pull request title: {title}\nFile: {name}\n\n{text}",
            ref=ref,
            source=f"diff of {name}",
            boundary=boundary,
        )
        for ref, (name, text) in files.items()
    ]
    return f"Review these {len(files)} changed files (refs {', '.join(files)}).\n\n" + "\n\n".join(
        blocks
    )
