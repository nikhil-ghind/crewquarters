"""Model file installation (PLAN.md section 8.2).

Files download into ``models/.staging/<id>-<revision>``. The daemon checks disk space
before starting, resumes partial files with HTTP Range, and verifies every file's size
and, where the source publishes one, its SHA-256. It writes a manifest, then atomically
renames the staging directory to ``models/<id>/<revision>``. A partial download is
resumable or can be cleared explicitly.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from crewquarters_runtime.specs import ModelProfile, SpecError

CHUNK = 1024 * 1024
STATES = ("NOT_INSTALLED", "DOWNLOADING", "INSTALLED", "DOWNLOAD_ERROR", "DELETING")


class _Cancelled(Exception):
    pass


class ModelStore:
    def __init__(self, models_dir: Path, hf_endpoint: str, disk_reserve_bytes: int) -> None:
        self.models_dir = models_dir
        self.hf_endpoint = hf_endpoint.rstrip("/")
        self.disk_reserve_bytes = disk_reserve_bytes
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: dict[str, threading.Event] = {}
        for sub in (".staging", ".state"):
            (models_dir / sub).mkdir(parents=True, exist_ok=True)

    # --- paths and state ------------------------------------------------------------

    def install_path(self, profile: ModelProfile) -> Path:
        return self.models_dir / profile.id / profile.revision

    def staging_path(self, profile: ModelProfile) -> Path:
        return self.models_dir / ".staging" / f"{profile.id}-{profile.revision}"

    def _state_file(self, model_id: str) -> Path:
        return self.models_dir / ".state" / f"{model_id}.json"

    def state(self, profile: ModelProfile) -> dict[str, Any]:
        path = self._state_file(profile.id)
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except ValueError:
                data = {}
        installed = (self.install_path(profile) / "crewquarters-manifest.json").exists()
        if data.get("revision") != profile.revision:
            data = {}
        if installed and data.get("state") not in ("DELETING",):
            data["state"] = "INSTALLED"
        elif not installed and data.get("state") in (None, "INSTALLED"):
            data["state"] = "NOT_INSTALLED"
        if data.get("state") == "DOWNLOADING" and profile.id not in self._threads:
            # The daemon restarted mid-download: the staging files are resumable.
            data["state"] = "DOWNLOAD_ERROR"
            data["error"] = {
                "code": "INTERRUPTED",
                "message": "Download interrupted; retry to resume.",
            }
        data.setdefault("bytesDone", 0)
        data.setdefault("bytesTotal", None)
        data["modelId"] = profile.id
        data["revision"] = profile.revision
        data["path"] = str(self.install_path(profile)) if data["state"] == "INSTALLED" else None
        return data

    def _write_state(self, profile: ModelProfile, **fields: Any) -> None:
        current = {}
        path = self._state_file(profile.id)
        if path.exists():
            try:
                current = json.loads(path.read_text())
            except ValueError:
                current = {}
        if current.get("revision") != profile.revision:
            current = {"revision": profile.revision}
        current.update(fields)
        current["updatedAt"] = time.time()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current))
        tmp.replace(path)

    # --- operations ----------------------------------------------------------------

    def start_install(self, profile: ModelProfile) -> dict[str, Any]:
        with self._lock:
            current = self.state(profile)
            if current["state"] == "INSTALLED" or profile.id in self._threads:
                return current
            cancel = threading.Event()
            self._cancel[profile.id] = cancel
            self._write_state(profile, state="DOWNLOADING", error=None)
            thread = threading.Thread(
                target=self._run_install,
                args=(profile, cancel),
                name=f"download-{profile.id}",
                daemon=True,
            )
            self._threads[profile.id] = thread
            thread.start()
            return self.state(profile)

    def cancel_install(self, profile: ModelProfile, clear: bool) -> dict[str, Any]:
        event = self._cancel.get(profile.id)
        if event:
            event.set()
        thread = self._threads.get(profile.id)
        if thread:
            thread.join(timeout=30)
        if clear:
            shutil.rmtree(self.staging_path(profile), ignore_errors=True)
            self._write_state(profile, state="NOT_INSTALLED", bytesDone=0, error=None)
        return self.state(profile)

    def wait(self, model_id: str, timeout: float) -> None:
        thread = self._threads.get(model_id)
        if thread:
            thread.join(timeout)

    def delete(self, profile: ModelProfile) -> dict[str, Any]:
        if profile.id in self._threads:
            raise SpecError("MODEL_BUSY", "Cancel the download before deleting the model.")
        self._write_state(profile, state="DELETING")
        shutil.rmtree(self.models_dir / profile.id, ignore_errors=True)
        shutil.rmtree(self.staging_path(profile), ignore_errors=True)
        self._write_state(profile, state="NOT_INSTALLED", bytesDone=0, bytesTotal=None, error=None)
        return self.state(profile)

    # --- worker ---------------------------------------------------------------------

    def _run_install(self, profile: ModelProfile, cancel: threading.Event) -> None:
        try:
            files = self._plan(profile)
            total = sum(int(f["size"]) for f in files)
            staging = self.staging_path(profile)
            staging.mkdir(parents=True, exist_ok=True)
            present = sum(
                min((staging / f["path"]).stat().st_size, int(f["size"]))
                for f in files
                if (staging / f["path"]).exists()
            )
            free = shutil.disk_usage(self.models_dir).free
            if total - present + self.disk_reserve_bytes > free:
                raise SpecError(
                    "INSUFFICIENT_DISK",
                    f"Need {total - present} bytes plus a {self.disk_reserve_bytes}-byte reserve; "
                    f"{free} free.",
                )
            self._write_state(profile, state="DOWNLOADING", bytesTotal=total, bytesDone=present)
            done = present
            for item in files:
                if cancel.is_set():
                    raise _Cancelled
                self._write_state(profile, currentFile=item["path"])
                done = self._fetch(profile, item, staging, done, cancel)
            manifest = {
                "modelId": profile.id,
                "revision": profile.revision,
                "files": [{k: f[k] for k in ("path", "size", "sha256")} for f in files],
            }
            (staging / "crewquarters-manifest.json").write_text(json.dumps(manifest, indent=2))
            _make_world_readable(staging)
            target = self.install_path(profile)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(target.parent, 0o755)  # noqa: S103 - model weights are not secret
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
            checksum = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
            self._write_state(
                profile,
                state="INSTALLED",
                bytesDone=total,
                currentFile=None,
                error=None,
                manifestSha256=checksum,
                verifiedAt=time.time(),
            )
        except _Cancelled:
            self._write_state(
                profile,
                state="DOWNLOAD_ERROR",
                error={"code": "CANCELLED", "message": "Download cancelled; retry to resume."},
            )
        except SpecError as exc:
            self._write_state(
                profile, state="DOWNLOAD_ERROR", error={"code": exc.code, "message": exc.message}
            )
        except Exception as exc:
            self._write_state(
                profile,
                state="DOWNLOAD_ERROR",
                error={"code": "DOWNLOAD_FAILED", "message": f"{type(exc).__name__}: {exc}"[:500]},
            )
        finally:
            with self._lock:
                self._threads.pop(profile.id, None)
                self._cancel.pop(profile.id, None)

    def _plan(self, profile: ModelProfile) -> list[dict[str, Any]]:
        source = profile.source
        kind = source.get("type")
        if kind == "bundled":
            return [
                {
                    "path": f["path"],
                    "size": int(f["size"]),
                    "sha256": f["sha256"],
                    "from": f["from"],
                }
                for f in source["files"]
            ]
        if kind == "huggingface":
            return self._hf_files(source)
        raise SpecError("UNSUPPORTED_SOURCE", f"Unsupported model source {kind!r}.")

    def _hf_files(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        repo, revision = source["repo"], source["revision"]
        if len(revision) != 40:
            raise SpecError("UNPINNED_REVISION", "Model revisions must be full commit hashes.")
        url = f"{self.hf_endpoint}/api/models/{repo}/tree/{revision}?recursive=true"
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https endpoint
            entries = json.load(response)
        patterns = source.get("allowPatterns") or ["*"]
        files = []
        for entry in entries:
            if entry.get("type") != "file":
                continue
            path = entry["path"]
            if not any(fnmatch.fnmatch(path, p) for p in patterns):
                continue
            if ".." in Path(path).parts or path.startswith("/"):
                raise SpecError("UNSAFE_PATH", f"Refusing unsafe file path {path!r}.")
            lfs = entry.get("lfs") or {}
            files.append(
                {
                    "path": path,
                    "size": int(lfs.get("size", entry.get("size", 0))),
                    "sha256": lfs.get("oid"),
                    "url": (
                        f"{self.hf_endpoint}/{repo}/resolve/{revision}/{urllib.parse.quote(path)}"
                    ),
                }
            )
        if not files:
            raise SpecError("EMPTY_MODEL", "No files matched the profile's allowPatterns.")
        return files

    def _fetch(
        self,
        profile: ModelProfile,
        item: dict[str, Any],
        staging: Path,
        done: int,
        cancel: threading.Event,
    ) -> int:
        dest = staging / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = int(item["size"])
        if "from" in item:  # bundled file shipped with the catalog
            source = (profile.catalog_dir / item["from"]).resolve()
            if profile.catalog_dir.resolve() not in source.parents:
                raise SpecError("UNSAFE_PATH", "Bundled files must live inside the catalog.")
            if dest.exists():  # already counted in "present"; recount after copying
                done -= min(dest.stat().st_size, size)
            shutil.copyfile(source, dest)
        else:
            have = dest.stat().st_size if dest.exists() else 0
            if have < size:
                request = urllib.request.Request(item["url"])  # noqa: S310
                if have:
                    request.add_header("Range", f"bytes={have}-")
                with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                    mode = "ab" if have and response.status == 206 else "wb"
                    if mode == "wb":
                        done -= have
                        have = 0
                    with dest.open(mode) as out:
                        while True:
                            if cancel.is_set():
                                raise _Cancelled
                            chunk = response.read(CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                            done += len(chunk)
                            self._write_state(profile, bytesDone=done)
        actual = dest.stat().st_size
        if actual != size:
            dest.unlink(missing_ok=True)
            raise SpecError("CHECKSUM_MISMATCH", f"{item['path']}: size {actual} != {size}.")
        if item.get("sha256"):
            digest = hashlib.sha256()
            with dest.open("rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK), b""):
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                dest.unlink(missing_ok=True)
                raise SpecError("CHECKSUM_MISMATCH", f"{item['path']}: SHA-256 mismatch.")
        else:
            item["sha256"] = None
        if "from" in item:
            done += size
        self._write_state(profile, bytesDone=done)
        return done


def _make_world_readable(root: Path) -> None:
    """Serving containers drop every capability (no CAP_DAC_OVERRIDE), so even a root
    vLLM process reads the bind-mounted weights through the "other" permission bits.
    Weights are public artifacts, not secrets."""
    for directory, _, files in os.walk(root):
        os.chmod(directory, 0o755)  # noqa: S103
        for name in files:
            os.chmod(os.path.join(directory, name), 0o644)
