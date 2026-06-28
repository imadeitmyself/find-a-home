"""Runs torrent downloads via aria2c and notifies Telegram on completion."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

from .config import Config
from .magnet import Magnet, parse_magnet
from .telegram import TelegramClient

logger = logging.getLogger("magnet_grab")

# aria2 control files we should not present as downloadable content.
_IGNORED_SUFFIXES = (".aria2",)


@dataclass
class DownloadedFile:
    relpath: str  # path relative to the job directory, using forward slashes
    size: int


@dataclass
class Job:
    infohash: str
    name: str
    magnet: str
    status: str = "queued"  # queued | downloading | done | error
    error: str = ""
    files: List[DownloadedFile] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("%.0f %s" % (value, unit)) if unit == "B" else ("%.1f %s" % (value, unit))
        value /= 1024
    return "%.1f TB" % value


def build_file_url(config: Config, infohash: str, relpath: str) -> str:
    encoded = "/".join(quote(part) for part in relpath.split("/"))
    url = "%s/%s/%s" % (config.file_base_url(), infohash, encoded)
    if config.access_token:
        url += "?token=%s" % quote(config.access_token)
    return url


def format_completion_message(config: Config, job: Job) -> str:
    header = "✅ Torrent ready: %s" % job.name
    summary = "%d file%s · %s" % (
        len(job.files),
        "" if len(job.files) == 1 else "s",
        human_size(job.total_size),
    )
    lines = [header, summary, ""]
    for downloaded in job.files:
        name = downloaded.relpath.rsplit("/", 1)[-1]
        lines.append("%s: %s" % (name, build_file_url(config, job.infohash, downloaded.relpath)))
    return "\n".join(lines)


def format_failure_message(job: Job) -> str:
    return "❌ Torrent failed: %s\n%s" % (job.name, job.error or "unknown error")


def collect_files(job_dir: Path) -> List[DownloadedFile]:
    files: List[DownloadedFile] = []
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in _IGNORED_SUFFIXES:
            continue
        relpath = path.relative_to(job_dir).as_posix()
        files.append(DownloadedFile(relpath=relpath, size=path.stat().st_size))
    return files


class Downloader:
    """Background job runner. Each magnet downloads into its own infohash folder."""

    def __init__(
        self,
        config: Config,
        telegram: Optional[TelegramClient] = None,
        runner: Optional[Callable[[List[str]], "subprocess.CompletedProcess"]] = None,
    ) -> None:
        self.config = config
        self.telegram = telegram
        self._runner = runner or self._run_aria2c
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    # -- public API ---------------------------------------------------------

    def jobs(self) -> List[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def submit(self, magnet_uri: str) -> Job:
        """Validate a magnet link and start downloading it in a background thread."""
        magnet = parse_magnet(magnet_uri)
        with self._lock:
            existing = self._jobs.get(magnet.infohash)
            if existing and existing.status in ("queued", "downloading"):
                return existing
            job = Job(infohash=magnet.infohash, name=magnet.display_name, magnet=magnet.uri)
            self._jobs[magnet.infohash] = job
        thread = threading.Thread(target=self._run_job, args=(job, magnet), daemon=True)
        thread.start()
        return job

    def run_sync(self, magnet_uri: str) -> Job:
        """Download synchronously (used by the CLI / tests)."""
        magnet = parse_magnet(magnet_uri)
        job = Job(infohash=magnet.infohash, name=magnet.display_name, magnet=magnet.uri)
        with self._lock:
            self._jobs[magnet.infohash] = job
        self._run_job(job, magnet)
        return job

    # -- internals ----------------------------------------------------------

    def _run_job(self, job: Job, magnet: Magnet) -> None:
        job.status = "downloading"
        job_dir = self.config.download_dir / job.infohash
        job_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Starting download %s (%s)", job.infohash, job.name)
        try:
            result = self._runner(self._aria2c_command(magnet, job_dir))
            if result.returncode != 0:
                raise RuntimeError(
                    "aria2c exited %d: %s"
                    % (result.returncode, (result.stderr or result.stdout or "").strip()[-500:])
                )
            job.files = collect_files(job_dir)
            if not job.files:
                raise RuntimeError("Download finished but no files were found")
            job.status = "done"
            job.finished_at = time.time()
            logger.info("Completed %s: %d files", job.infohash, len(job.files))
            self._notify(format_completion_message(self.config, job))
        except FileNotFoundError as exc:
            job.status = "error"
            job.error = "aria2c not found (install it: apt install aria2). %s" % exc
            job.finished_at = time.time()
            logger.error("aria2c missing: %s", exc)
            self._notify(format_failure_message(job))
        except Exception as exc:  # noqa: BLE001 - report every failure to Telegram
            job.status = "error"
            job.error = str(exc)
            job.finished_at = time.time()
            logger.error("Download %s failed: %s", job.infohash, exc)
            self._notify(format_failure_message(job))

    def _aria2c_command(self, magnet: Magnet, job_dir: Path) -> List[str]:
        return [
            self.config.aria2c_path,
            "--dir=%s" % job_dir,
            "--seed-time=0",  # stop as soon as the download completes
            "--bt-stop-timeout=900",  # give up if no progress for 15 minutes
            "--summary-interval=0",
            "--console-log-level=warn",
            "--show-console-readout=false",
            magnet.uri,
        ]

    def _run_aria2c(self, command: List[str]) -> "subprocess.CompletedProcess":
        return subprocess.run(command, capture_output=True, text=True)

    def _notify(self, message: str) -> None:
        if not self.telegram:
            logger.info("Telegram disabled; message:\n%s", message)
            return
        try:
            self.telegram.send_message(message)
        except Exception as exc:  # noqa: BLE001 - notification must not crash the worker
            logger.error("Telegram notification failed: %s", exc)
