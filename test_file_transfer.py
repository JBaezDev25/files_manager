#!/usr/bin/env python3
"""Unit tests for file_transfer module."""

import os
import tempfile
from pathlib import Path

import pytest

from file_transfer import (
    compute_checksum,
    copy_file,
    copy_path,
    expand_source_spec,
    is_online,
    total_bytes,
    verify_checksum,
    verify_copy,
)


class TestComputeChecksum:
    def test_sha256(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("hello world")
            f.flush()
            path = Path(f.name)

        checksum = compute_checksum(path, "sha256")
        assert (
            checksum
            == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )
        os.unlink(path)

    def test_md5(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("hello world")
            f.flush()
            path = Path(f.name)

        checksum = compute_checksum(path, "md5")
        assert checksum == "5eb63bbbe01eeed093cb22bb8f5acdc3"
        os.unlink(path)


class TestVerifyChecksum:
    def test_identical_files(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f1:
            f1.write("test content")
            f1.flush()
            src = Path(f1.name)

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f2:
            f2.write("test content")
            f2.flush()
            dst = Path(f2.name)

        assert verify_checksum(src, dst) is True
        os.unlink(src)
        os.unlink(dst)

    def test_different_files(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f1:
            f1.write("content A")
            f1.flush()
            src = Path(f1.name)

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f2:
            f2.write("content B")
            f2.flush()
            dst = Path(f2.name)

        assert verify_checksum(src, dst) is False
        os.unlink(src)
        os.unlink(dst)

    def test_missing_destination(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f1:
            f1.write("content")
            f1.flush()
            src = Path(f1.name)

        dst = Path("/tmp/nonexistent_file_12345")
        assert verify_checksum(src, dst) is False
        os.unlink(src)


class TestTotalBytes:
    def test_single_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("x" * 100)
            f.flush()
            path = Path(f.name)

        assert total_bytes([path]) == 100
        os.unlink(path)

    def test_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "f1.txt").write_text("a" * 10)
            (p / "f2.txt").write_text("b" * 20)

            assert total_bytes([p]) == 30


class TestCopyFile:
    def test_copy_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("test content")
            f.flush()
            src = Path(f.name)

        with tempfile.TemporaryDirectory() as tmpdir:
            dst = Path(tmpdir) / "dest.txt"
            progress = {"done": 0, "total": 12}
            copy_file(src, dst, progress)

            assert dst.exists()
            assert dst.read_text() == "test content"
            assert progress["done"] == 12

        os.unlink(src)


class TestCopyPath:
    def test_copy_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "source"
            src_dir.mkdir()
            (src_dir / "file1.txt").write_text("content1")
            (src_dir / "file2.txt").write_text("content2")

            dest_dir = Path(tmpdir) / "dest"
            progress = {"done": 0, "total": 16}
            copy_path(src_dir, dest_dir, progress)

            copied_file = dest_dir / "source" / "file1.txt"
            assert copied_file.exists()
            assert copied_file.read_text() == "content1"


class TestVerifyCopy:
    def test_verify_copy_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "source"
            src_dir.mkdir()
            (src_dir / "file1.txt").write_text("content1")

            dest_dir = Path(tmpdir) / "dest"
            copy_path(src_dir, dest_dir, {"done": 0, "total": 8})

            assert verify_copy(src_dir, dest_dir) is True


class TestExpandSourceSpec:
    def test_existing_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)

        result = expand_source_spec(str(path))
        assert result == [path.resolve()]
        os.unlink(path)

    def test_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            expand_source_spec("/tmp/nonexistent_file_xyz")


class TestIsOnline:
    def test_is_online(self):
        assert is_online() is True

    def test_is_online_invalid_host(self):
        assert is_online(host="192.0.2.1", timeout=1) is False
