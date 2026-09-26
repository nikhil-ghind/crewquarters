import pytest

from crewquarters_fake.errors import ApiError
from crewquarters_fake.providers.github import GitHubProvider, right_side_lines

PATCH = "@@ -1,3 +1,4 @@\n a\n-b\n+B\n+C\n d\n"


def provider() -> GitHubProvider:
    github = GitHubProvider()
    github.load(
        [
            {"number": 1, "title": "old", "sha": "a" * 40, "updatedAt": "2026-01-01T00:00:00Z"},
            {
                "number": 2,
                "title": "new",
                "sha": "b" * 40,
                "updatedAt": "2026-02-01T00:00:00Z",
                "files": [{"filename": "x.py", "patch": PATCH}],
            },
        ]
    )
    return github


def test_right_side_lines_are_added_and_unchanged_lines_only() -> None:
    assert right_side_lines(PATCH) == {1, 2, 3, 4}
    assert right_side_lines("@@ -5,2 +5,1 @@\n-gone\n keep\n") == {5}
    assert right_side_lines("no hunk here") == set()


def test_pulls_come_newest_first_and_respect_the_limit() -> None:
    assert [p["number"] for p in provider().list_pulls(10)["pulls"]] == [2, 1]
    assert [p["number"] for p in provider().list_pulls(1)["pulls"]] == [2]


def test_files_count_additions_and_deletions() -> None:
    (file,) = provider().list_files(2)["files"]
    assert (file["additions"], file["deletions"]) == (2, 1)


def test_unknown_pulls_are_not_found() -> None:
    with pytest.raises(ApiError) as caught:
        provider().list_files(99)
    assert caught.value.status == 404


def test_a_review_is_kept_and_listed_and_a_bad_line_is_refused() -> None:
    github = provider()
    posted = github.create_review(2, "b" * 40, "body", [{"path": "x.py", "line": 3, "body": "c"}])
    assert github.list_reviews(2)["reviews"][0]["id"] == posted["id"]
    with pytest.raises(ApiError) as caught:
        github.create_review(2, "b" * 40, "body", [{"path": "x.py", "line": 50, "body": "c"}])
    assert caught.value.status == 422 and caught.value.code == "INVALID_REQUEST"
    with pytest.raises(ApiError):
        github.create_review(2, "b" * 40, "body", [{"path": "other.py", "line": 1, "body": "c"}])
    assert len(github.list_reviews(2)["reviews"]) == 1
    assert list(github.snapshot()) == ["2"]
