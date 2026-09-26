from crewquarters.github import PullFile
from pr_reviewer.diff import annotate
from pr_reviewer.models import FileReview, Finding, ReviewedFinding
from pr_reviewer.review import (
    ReviewFile,
    batches,
    clean,
    comment_body,
    marker,
    review_body,
    reviewable,
    select_findings,
)

PATCH = "@@ -1,2 +1,4 @@\n a\n+b\n+c\n d\n"


def file(name: str, patch: str | None = PATCH, status: str = "modified") -> PullFile:
    return PullFile(name, status, 2, 0, patch)


def finding(
    line: int, category: str = "bug", severity: str = "high", comment: str = "x"
) -> Finding:
    return Finding.model_validate(
        {"line": line, "category": category, "severity": severity, "comment": comment}
    )


def test_reviewable_skips_lockfiles_binaries_removed_and_patchless_files() -> None:
    files = [
        file("src/a.py"),
        file("uv.lock"),
        file("web/package-lock.json"),
        file("logo.png"),
        file("gone.py", status="removed"),
        file("big.bin", patch=None),
        file("src/b.py"),
    ]
    chosen, left_out = reviewable(files, max_files=10, max_chars=1000)
    assert [(f.ref, f.name) for f in chosen] == [("f1", "src/a.py"), ("f2", "src/b.py")]
    assert left_out == 5


def test_reviewable_honours_the_file_cap() -> None:
    chosen, left_out = reviewable([file(f"s{i}.py") for i in range(5)], max_files=2, max_chars=1000)
    assert len(chosen) == 2 and left_out == 3


def test_batches_pack_by_size_and_never_split_a_file() -> None:
    files = [ReviewFile(f"f{i}", f"s{i}.py", annotate(PATCH, 1000)) for i in range(3)]
    size = len(files[0].patch.text)
    assert [len(b) for b in batches(files, size * 2)] == [2, 1]
    assert [len(b) for b in batches(files, 1)] == [1, 1, 1]


def selected(reviews: dict[str, FileReview], limit: int = 10) -> tuple[list[ReviewedFinding], int]:
    files = [ReviewFile("f1", "a.py", annotate(PATCH, 1000))]
    return select_findings(files, reviews, limit)


def test_findings_outside_the_diff_or_for_unknown_files_are_dropped() -> None:
    kept, dropped = selected(
        {
            "f1": FileReview(ref="f1", findings=[finding(2), finding(99), finding(3, comment=" ")]),
            "f9": FileReview(ref="f9", findings=[finding(2)]),
        }
    )
    assert [(f.path, f.line) for f in kept] == [("a.py", 2)]
    assert dropped == 3


def test_one_comment_per_line_and_bugs_rank_before_style() -> None:
    kept, dropped = selected(
        {
            "f1": FileReview(
                ref="f1",
                findings=[
                    finding(3, "style", "low"),
                    finding(2, "style", "high"),
                    finding(2, "bug", "low", "real bug"),
                ],
            )
        }
    )
    assert [(f.line, f.category, f.comment) for f in kept] == [
        (2, "bug", "real bug"),
        (3, "style", "x"),
    ]
    assert dropped == 1


def test_the_comment_cap_keeps_the_most_important() -> None:
    kept, dropped = selected(
        {"f1": FileReview(ref="f1", findings=[finding(1, "style"), finding(2, "bug")])}, limit=1
    )
    assert [f.category for f in kept] == ["bug"] and dropped == 1


def test_model_text_cannot_ping_hide_html_or_offer_a_suggestion_commit() -> None:
    dirty = "cc @octocat <!-- hidden --> ```suggestion\nx\n```"
    text = clean(dirty)
    assert "@octocat" not in text and "<!--" not in text and "```suggestion" not in text.lower()
    body = comment_body(
        ReviewedFinding(
            path="a.py", line=2, category="bug", severity="high", comment="@a bad", suggestion="@b"
        )
    )
    assert body.startswith("**Bug · high**") and "@a" not in body and "@b" not in body


def test_review_body_carries_the_marker_and_can_list_findings() -> None:
    f = ReviewedFinding(path="a.py", line=2, category="style", severity="low", comment="c")
    assert marker("abc1234def") in review_body("abc1234def", [f])
    assert "`a.py:2`" in review_body("abc1234def", [f], inline=False)
    assert "`a.py:2`" not in review_body("abc1234def", [f])
