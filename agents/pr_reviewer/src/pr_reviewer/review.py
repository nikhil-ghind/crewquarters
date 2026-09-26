"""Turning a pull request's files into vetted findings, and findings into GitHub comment text.

The model only proposes. Plain code decides what is kept: a finding survives only when its file
was in the review and its line is one the diff shows, so a hallucinated location never reaches
GitHub."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from crewquarters.errors import InvalidInput
from crewquarters.github import PullFile
from crewquarters.llm import ChatResult
from pr_reviewer.diff import AnnotatedPatch, annotate
from pr_reviewer.models import FileReview, ReviewBatch, ReviewedFinding
from pr_reviewer.prompts import REPAIR, batch_prompt, system_prompt

# Files with nothing a reviewer can usefully say about a diff.
SKIPPED_SUFFIXES = (
    ".lock",
    ".min.js",
    ".min.css",
    ".map",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".snap",
)
SKIPPED_NAMES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock"})
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
CATEGORY_ORDER = {"bug": 0, "style": 1}
_MENTION = re.compile(r"@(?=\w)")


@dataclass(frozen=True)
class ReviewFile:
    ref: str
    name: str
    patch: AnnotatedPatch


def reviewable(
    files: list[PullFile], max_files: int, max_chars: int
) -> tuple[list[ReviewFile], int]:
    """The files worth sending to the model, and how many were left out."""
    chosen: list[ReviewFile] = []
    for file in files:
        name = file.filename
        if (
            not file.patch
            or file.status == "removed"
            or name.rsplit("/", 1)[-1] in SKIPPED_NAMES
            or name.lower().endswith(SKIPPED_SUFFIXES)
        ):
            continue
        patch = annotate(file.patch, max_chars)
        if patch.lines and len(chosen) < max_files:
            chosen.append(ReviewFile(f"f{len(chosen) + 1}", name, patch))
    return chosen, len(files) - len(chosen)


def batches(files: list[ReviewFile], max_chars: int) -> list[list[ReviewFile]]:
    """Pack files into batches of at most ``max_chars`` of diff (an oversized file goes alone)."""
    packed: list[list[ReviewFile]] = []
    size = 0
    for file in files:
        if not packed or size + len(file.patch.text) > max_chars:
            packed.append([])
            size = 0
        packed[-1].append(file)
        size += len(file.patch.text)
    return packed


async def review_batch(
    ctx: Any,
    group: list[ReviewFile],
    *,
    title: str,
    style_guide: str,
    boundary: str,
    profile: str,
    key: str,
) -> tuple[dict[str, FileReview], ChatResult | None]:
    """Ask the model about one batch. A reply that fails the schema gets one repair retry; a
    second failure yields no findings for the batch rather than failing the run."""
    prompt: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt(style_guide)},
        {
            "role": "user",
            "content": batch_prompt(
                title, {f.ref: (f.name, f.patch.text) for f in group}, boundary
            ),
        },
    ]
    for attempt in (1, 2):
        try:
            chat: ChatResult = await ctx.llm.chat(
                profile,
                prompt,
                temperature=0,
                response_model=ReviewBatch,
                idempotency_key=f"{key}-a{attempt}-v1",
            )
        except InvalidInput as exc:
            if exc.code != "STRUCTURED_OUTPUT_INVALID":
                raise
            await ctx.events.log("warning", f"{key}: output did not match the schema ({attempt})")
            if attempt == 1:
                prompt = [
                    *prompt,
                    {"role": "assistant", "content": str(exc.details.get("raw", ""))},
                    {"role": "user", "content": REPAIR},
                ]
            continue
        reviews: dict[str, FileReview] = {}
        for item in chat.parsed.files:
            reviews.setdefault(item.ref, item)
        return reviews, chat
    return {}, None


def select_findings(
    files: list[ReviewFile], reviews: dict[str, FileReview], limit: int
) -> tuple[list[ReviewedFinding], int]:
    """Keep findings that point at a real diff line, dedupe, rank (bugs first, then severity),
    and cap at ``limit``. Returns the kept findings and how many were dropped."""
    by_ref = {f.ref: f for f in files}
    candidates: list[ReviewedFinding] = []
    dropped = 0
    for ref, review in reviews.items():
        file = by_ref.get(ref)
        for finding in review.findings:
            comment = finding.comment.strip()
            if file is None or finding.line not in file.patch.lines or not comment:
                dropped += 1
                continue
            candidates.append(
                ReviewedFinding(
                    path=file.name,
                    line=finding.line,
                    category=finding.category,
                    severity=finding.severity,
                    comment=comment,
                    suggestion=(finding.suggestion or "").strip() or None,
                )
            )
    best: dict[tuple[str, int], ReviewedFinding] = {}
    for candidate in sorted(candidates, key=_rank):
        if (candidate.path, candidate.line) in best:
            dropped += 1  # one comment per line: the most important one wins
        else:
            best[(candidate.path, candidate.line)] = candidate
    ranked = sorted(best.values(), key=lambda f: (*_rank(f), f.path, f.line))
    return ranked[:limit], dropped + max(0, len(ranked) - limit)


def _rank(finding: ReviewedFinding) -> tuple[int, int]:
    return CATEGORY_ORDER[finding.category], SEVERITY_ORDER[finding.severity]


def clean(text: str) -> str:
    """Model text is untrusted once it is posted: no pings, hidden HTML, or one-click
    ``suggestion`` blocks that GitHub would offer to commit."""
    text = _MENTION.sub("@​", text)
    text = text.replace("<!--", "").replace("-->", "")
    return re.sub(r"```\s*suggestion", "``` text", text, flags=re.IGNORECASE)


def comment_body(finding: ReviewedFinding) -> str:
    label = "Bug" if finding.category == "bug" else "Style"
    body = f"**{label} · {finding.severity}** — {clean(finding.comment)}"
    if finding.suggestion:
        body += f"\n\n_Suggestion:_ {clean(finding.suggestion)}"
    return body


def marker(head_sha: str) -> str:
    return f"<!-- crewquarters-pr-reviewer sha={head_sha} -->"


def review_body(head_sha: str, findings: list[ReviewedFinding], inline: bool = True) -> str:
    bugs = sum(1 for f in findings if f.category == "bug")
    style = len(findings) - bugs
    lines = [
        f"Automated review of `{head_sha[:7]}` by Crewquarters PR Reviewer: "
        f"{bugs} possible bug(s), {style} style note(s).",
        "These come from an AI model and may be wrong; please check before acting on them.",
    ]
    if not inline:
        lines += ["", *(f"- `{f.path}:{f.line}` {comment_body(f)}" for f in findings)]
    return "\n".join(lines) + f"\n\n{marker(head_sha)}"
