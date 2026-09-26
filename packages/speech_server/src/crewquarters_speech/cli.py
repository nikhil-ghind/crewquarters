"""crewq-speech: serve the speech API or download its models."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import click
import uvicorn

from crewquarters_speech.app import create_app
from crewquarters_speech.catalog import STT_MODELS, TTS_MODELS
from crewquarters_speech.download import DownloadError, download
from crewquarters_speech.settings import SpeechSettings


@click.group()
def cli() -> None:
    """Crewquarters speech server (OpenAI-compatible speech-to-text and text-to-speech)."""


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8200, show_default=True, type=int)
def serve(host: str, port: int) -> None:
    """Serve the API (settings come from CREWQ_SPEECH_* environment variables)."""
    uvicorn.run(create_app(SpeechSettings.from_env()), host=host, port=port, log_level="info")


@cli.command("download")
@click.option(
    "--models-dir", type=click.Path(path_type=Path), help="Default: CREWQ_SPEECH_MODELS_DIR"
)
def download_command(models_dir: Path | None) -> None:
    """Download and verify the configured speech-to-text and speech models."""
    settings = SpeechSettings.from_env()
    if models_dir is not None:
        settings = replace(settings, models_dir=models_dir)
    try:
        for archive in (STT_MODELS[settings.stt_model], TTS_MODELS[settings.tts_model]):
            download(archive, settings.models_dir, progress=click.echo)
    except (DownloadError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"models ready in {settings.models_dir}")


def main() -> None:
    cli()
