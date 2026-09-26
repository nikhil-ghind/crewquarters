"""pr-reviewer: reviews the latest open pull requests for likely bugs and style problems."""

from __future__ import annotations

from crewquarters import Agent, RunContext
from crewquarters.errors import (
    AgentError,
    InvalidInput,
    NeedsConnection,
    OutcomeUnknown,
    PlatformError,
)
from crewquarters.github import PullRequest, ReviewComment
from crewquarters.untrusted import new_boundary
from pr_reviewer.config import ReviewConfig
from pr_reviewer.models import Counts, ModelInfo, PullResult, ReviewedFinding, ReviewResult
from pr_reviewer.review import (
    batches,
    comment_body,
    marker,
    review_batch,
    review_body,
    reviewable,
    select_findings,
)

agent = Agent("pr-reviewer", config_model=ReviewConfig, result_model=ReviewResult)
CONNECT_HINT = "GitHub is not connected: set CQ_GITHUB_TOKEN for the capability broker"


async def _already_reviewed(ctx: RunContext[ReviewConfig], pull: PullRequest) -> bool:
    reviews = await ctx.github.list_reviews(ctx.config.repo, pull.number)
    return any(marker(pull.head_sha) in review.body for review in reviews)


async def _post(
    ctx: RunContext[ReviewConfig], pull: PullRequest, findings: list[ReviewedFinding]
) -> tuple[str | None, str | None]:
    """Post one COMMENT review; returns ``(review_url, note)``. Never retried on an unknown
    outcome, so a review is never posted twice."""
    comments = [ReviewComment(f.path, f.line, comment_body(f)) for f in findings]
    try:
        try:
            created = await ctx.github.create_review(
                ctx.config.repo,
                pull.number,
                commit_id=pull.head_sha,
                body=review_body(pull.head_sha, findings),
                comments=comments,
            )
        except InvalidInput:
            # GitHub refused an inline position (for example the PR moved on): fall back to one
            # review that lists the findings in its text.
            await ctx.events.log("warning", f"PR #{pull.number}: inline comments refused")
            created = await ctx.github.create_review(
                ctx.config.repo,
                pull.number,
                commit_id=pull.head_sha,
                body=review_body(pull.head_sha, findings, inline=False),
                comments=[],
            )
            return created.url, "Inline comments were refused; findings are listed in the review."
    except OutcomeUnknown:
        await ctx.events.log("warning", f"PR #{pull.number}: review may or may not have posted")
        return None, "GitHub did not confirm the review; check the pull request before re-running."
    return created.url, None


@agent.run
async def run(ctx: RunContext[ReviewConfig]) -> ReviewResult:
    config = ctx.config
    dry_run = not config.post_comments
    await ctx.events.progress(5, f"Listing open pull requests in {config.repo}", step="list")
    try:
        # Ask for extra so skipped drafts do not shrink the review below maxPrs.
        listed = await ctx.github.list_pulls(config.repo, limit=min(50, config.max_prs * 2))
    except NeedsConnection as exc:
        raise AgentError(CONNECT_HINT, code="GITHUB_NOT_CONNECTED") from exc
    except PlatformError as exc:
        if exc.code == "NOT_FOUND":
            raise AgentError(
                f"{config.repo} was not found, or the token cannot see it", code="REPO_NOT_FOUND"
            ) from exc
        raise
    pulls = [p for p in listed if not (config.skip_drafts and p.draft)][: config.max_prs]

    boundary = new_boundary()
    model = ModelInfo(profile=config.model_profile)
    results: list[PullResult] = []
    for index, pull in enumerate(pulls):
        result = PullResult(
            number=pull.number,
            title=pull.title,
            author=pull.author,
            url=pull.url,
            head_sha=pull.head_sha,
            status="reviewed",
        )
        results.append(result)
        try:
            if await _already_reviewed(ctx, pull):
                result.status, result.skip_reason = "skipped", "Already reviewed at this commit"
                continue
            files, truncated = await ctx.github.list_files(config.repo, pull.number)
            chosen, left_out = reviewable(
                files, config.max_files_per_pr, config.max_diff_chars_per_file
            )
            result.files_reviewed, result.files_skipped = len(chosen), left_out
            result.truncated = truncated or any(f.patch.truncated for f in chosen)
            if not chosen:
                result.status, result.skip_reason = "skipped", "No reviewable text changes"
                continue
            reviews = {}
            for number, group in enumerate(batches(chosen, config.max_chars_per_batch)):
                found, chat = await review_batch(
                    ctx,
                    group,
                    title=pull.title,
                    style_guide=config.style_guide,
                    boundary=boundary,
                    profile=config.model_profile,
                    key=f"pr-{pull.number}-{pull.head_sha[:12]}-b{number}",
                )
                reviews.update(found)
                if chat is not None:
                    model = ModelInfo(
                        profile=config.model_profile,
                        provider=chat.provider,
                        model=chat.model,
                        locality=chat.locality,
                    )
            findings, dropped = select_findings(chosen, reviews, config.max_comments_per_pr)
            result.findings, result.dropped_findings = findings, dropped
            if findings and config.post_comments:
                result.review_url, result.post_note = await _post(ctx, pull, findings)
                result.posted = result.review_url is not None
        except NeedsConnection as exc:
            raise AgentError(CONNECT_HINT, code="GITHUB_NOT_CONNECTED") from exc
        except PlatformError as exc:
            if exc.code in {"MODEL_UNAVAILABLE", "CAPABILITY_DENIED", "RATE_LIMITED"}:
                raise
            # One bad pull request (deleted, permission) must not sink the others.
            await ctx.events.log("warning", f"PR #{pull.number} skipped: {exc.code}")
            result.status, result.skip_reason = "skipped", f"Could not be reviewed ({exc.code})"
        finally:
            await ctx.events.progress(
                10 + 85 * (index + 1) / len(pulls), f"Reviewed PR #{pull.number}", step="review"
            )

    reviewed = [r for r in results if r.status == "reviewed"]
    everything = [f for r in results for f in r.findings]
    counts = Counts(
        pulls=len(results),
        reviewed=len(reviewed),
        skipped=len(results) - len(reviewed),
        findings=len(everything),
        bugs=sum(1 for f in everything if f.category == "bug"),
        style=sum(1 for f in everything if f.category == "style"),
        posted=sum(1 for r in results if r.posted),
    )
    await ctx.events.metric("pull_requests_reviewed", counts.reviewed, unit="pulls")
    await ctx.events.metric("findings", counts.findings, unit="findings")
    await ctx.events.artifact(
        "review",
        "application/json",
        summary=(
            f"{counts.reviewed} PRs reviewed, {counts.bugs} possible bugs, {counts.style} style "
            f"notes, {counts.posted} reviews posted" + (" (dry run)" if dry_run else "")
        ),
    )
    await ctx.events.progress(100, "Review complete", step="done")
    return ReviewResult(
        repo=config.repo, dry_run=dry_run, counts=counts, pulls=results, model=model
    )
