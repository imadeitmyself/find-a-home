import subprocess
import tempfile
import unittest
from pathlib import Path

from magnet_grab.config import Config, load_config
from magnet_grab.downloader import (
    Downloader,
    build_file_url,
    collect_files,
    format_completion_message,
    human_size,
)
from magnet_grab.magnet import parse_magnet
from magnet_grab.server import _parse_range, _safe_join
from magnet_grab.telegram import TELEGRAM_MAX_CHARS, chunk_text

SAMPLE = "magnet:?xt=urn:btih:297C945AB4913AE6D215AA1FD61739A8B9A12534&dn=Cool+Release"


def make_config(download_dir: Path, token: str = "secret") -> Config:
    return Config(
        host="0.0.0.0",
        port=8800,
        download_dir=download_dir,
        public_url="http://vps.example.com:8800",
        access_token=token,
        telegram_token="",
        telegram_chat_id="",
        aria2c_path="aria2c",
        request_timeout=20,
    )


class MagnetParsingTests(unittest.TestCase):
    def test_parses_hex_infohash_and_display_name(self):
        magnet = parse_magnet(SAMPLE)
        self.assertEqual(magnet.infohash, "297c945ab4913ae6d215aa1fd61739a8b9a12534")
        self.assertEqual(magnet.display_name, "Cool Release")

    def test_display_name_defaults_to_infohash(self):
        magnet = parse_magnet("magnet:?xt=urn:btih:297C945AB4913AE6D215AA1FD61739A8B9A12534")
        self.assertEqual(magnet.display_name, magnet.infohash)

    def test_base32_infohash_is_normalized_to_hex(self):
        # 32-char base32 form of a known 20-byte hash.
        magnet = parse_magnet("magnet:?xt=urn:btih:MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43U")
        self.assertEqual(len(magnet.infohash), 40)
        int(magnet.infohash, 16)  # must be valid hex

    def test_rejects_non_magnet(self):
        with self.assertRaises(ValueError):
            parse_magnet("https://example.com/not-a-magnet")

    def test_rejects_magnet_without_infohash(self):
        with self.assertRaises(ValueError):
            parse_magnet("magnet:?dn=NoHashHere")


class HumanSizeTests(unittest.TestCase):
    def test_formats_bytes_and_gigabytes(self):
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(5 * 1024 ** 3), "5.0 GB")


class FileUrlTests(unittest.TestCase):
    def test_url_includes_token_and_encodes_path(self):
        config = make_config(Path("/tmp/x"), token="t0k")
        url = build_file_url(config, "abc123", "Season 1/Ep 01.mkv")
        self.assertEqual(
            url,
            "http://vps.example.com:8800/files/abc123/Season%201/Ep%2001.mkv?token=t0k",
        )

    def test_url_without_token(self):
        config = make_config(Path("/tmp/x"), token="")
        url = build_file_url(config, "abc", "file.bin")
        self.assertEqual(url, "http://vps.example.com:8800/files/abc/file.bin")


class CompletionMessageTests(unittest.TestCase):
    def test_one_line_per_file_with_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_config(root, token="")
            downloader = Downloader(config, runner=_fake_runner(root, ["a.mkv", "sub/b.srt"]))
            job = downloader.run_sync(SAMPLE)
            self.assertEqual(job.status, "done")
            message = format_completion_message(config, job)
            lines = message.splitlines()
            self.assertIn("✅ Torrent ready: Cool Release", lines[0])
            file_lines = [ln for ln in lines if "http://" in ln]
            self.assertEqual(len(file_lines), 2)
            self.assertTrue(any("a.mkv:" in ln and "/files/" in ln for ln in file_lines))
            self.assertTrue(any("b.srt:" in ln for ln in file_lines))


class DownloaderTests(unittest.TestCase):
    def test_failure_is_recorded_when_aria2_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), token="")

            def runner(cmd):
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="boom")

            job = Downloader(config, runner=runner).run_sync(SAMPLE)
            self.assertEqual(job.status, "error")
            self.assertIn("boom", job.error)

    def test_collect_files_skips_aria2_control_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "movie.mkv").write_bytes(b"x" * 10)
            (d / "movie.mkv.aria2").write_bytes(b"ctrl")
            files = collect_files(d)
            self.assertEqual([f.relpath for f in files], ["movie.mkv"])


class SafeJoinTests(unittest.TestCase):
    def test_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertIsNone(_safe_join(root, "../etc/passwd"))
            self.assertIsNone(_safe_join(root, "foo/../../etc/passwd"))

    def test_absolute_looking_path_is_clamped_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            joined = _safe_join(root, "/etc/passwd")
            # Leading slash is stripped and the path stays under the download root.
            self.assertIsNotNone(joined)
            joined.relative_to(root)  # raises if it escaped

    def test_allows_nested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "hash").mkdir()
            (root / "hash" / "file.bin").write_bytes(b"x")
            joined = _safe_join(root, "hash/file.bin")
            self.assertIsNotNone(joined)
            self.assertTrue(joined.is_file())


class RangeParsingTests(unittest.TestCase):
    def test_parses_explicit_range(self):
        self.assertEqual(_parse_range("bytes=0-99", 1000), (0, 99))

    def test_open_ended_range(self):
        self.assertEqual(_parse_range("bytes=500-", 1000), (500, 999))

    def test_suffix_range(self):
        self.assertEqual(_parse_range("bytes=-200", 1000), (800, 999))

    def test_invalid_range_returns_none(self):
        self.assertIsNone(_parse_range("bytes=2000-3000", 1000))


class ConfigTests(unittest.TestCase):
    def test_load_config_reuses_find_a_home_telegram_vars(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "tok",
            "TELEGRAM_CHAT_ID": "123",
            "MAGNET_GRAB_PUBLIC_URL": "http://vps:8800/",
            "MAGNET_GRAB_TOKEN": "s",
        }
        config = load_config(env)
        self.assertTrue(config.telegram_enabled)
        self.assertEqual(config.public_url, "http://vps:8800")
        self.assertEqual(config.file_base_url(), "http://vps:8800/files")


class ChunkTextTests(unittest.TestCase):
    def test_long_message_splits_under_limit(self):
        message = "\n".join("line %d %s" % (i, "x" * 80) for i in range(200))
        chunks = chunk_text(message)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), TELEGRAM_MAX_CHARS)
        self.assertEqual("\n".join(chunks), message)


def _fake_runner(root: Path, files):
    """Returns a runner that writes fake downloaded files for the job's infohash dir."""

    def runner(cmd):
        # The --dir argument tells us where aria2 would have written files.
        job_dir = None
        for arg in cmd:
            if arg.startswith("--dir="):
                job_dir = Path(arg[len("--dir="):])
        assert job_dir is not None
        for rel in files:
            target = job_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"data")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="ok", stderr="")

    return runner


if __name__ == "__main__":
    unittest.main()
