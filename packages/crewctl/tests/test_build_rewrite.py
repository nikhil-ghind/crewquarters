from pathlib import Path

from crewctl.build import build_command, find_repo_root, image_repo, rewrite_image_line

DIGEST = "sha256:" + "d" * 64


def test_rewrite_image_line_changes_only_the_image() -> None:
    text = (
        "# top comment\n"
        "spec:\n"
        "  image: localhost:5001/crewquarters/x@sha256:REQUIRED_DIGEST  # pinned by crewctl\n"
        "  entrypoint: [python, -m, x]\n"
    )
    updated = rewrite_image_line(text, f"localhost:5001/crewquarters/x@{DIGEST}")
    assert updated == text.replace("sha256:REQUIRED_DIGEST", DIGEST)


def test_rewrite_image_line_requires_an_image_key() -> None:
    try:
        rewrite_image_line("spec: {}\n", "x@" + DIGEST)
    except ValueError as exc:
        assert "image" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_image_repo() -> None:
    assert image_repo("localhost:5001", "caller") == "localhost:5001/crewquarters/caller"
    assert image_repo("ghcr.io/acme/", "caller") == "ghcr.io/acme/crewquarters/caller"


def test_build_command_push_and_load(tmp_path: Path) -> None:
    push = build_command(
        context=tmp_path,
        dockerfile=tmp_path / "agents/x/Dockerfile",
        tag="localhost:5001/crewquarters/x:0.1.0",
        platforms="linux/amd64,linux/arm64",
        push=True,
        metadata_file=tmp_path / "meta.json",
    )
    assert push[:3] == ["docker", "buildx", "build"]
    assert push[push.index("--platform") : push.index("--platform") + 2] == [
        "--platform",
        "linux/amd64,linux/arm64",
    ]
    assert "--push" in push and "--load" not in push
    assert push[-1] == str(tmp_path)
    assert "--metadata-file" in push
    load = build_command(
        context=tmp_path,
        dockerfile=tmp_path / "Dockerfile",
        tag="t",
        platforms="linux/arm64",
        push=False,
        metadata_file=None,
    )
    assert "--load" in load and "--push" not in load


def test_find_repo_root() -> None:
    here = Path(__file__).resolve()
    root = find_repo_root(here.parent)
    assert (root / "packages" / "python_sdk").is_dir()
