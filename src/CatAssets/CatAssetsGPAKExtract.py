"""Python translation of Program.cs GPAK extractor.

Original C# source: https://github.com/ShootMe/GPAK-Extractor

GPAK binary layout
------------------
  [4 bytes]  int32  count
  For each entry:
    [2 bytes] int16  name_len
    [name_len bytes]  name (UTF-8)
    [4 bytes] int32  data_len
  <file data follows, all entries concatenated>

Usage
-----
  from CatAssetsGPAKExtract import extract_entry
  raw = extract_entry(gpak_path, "swfs/catparts.swf")
"""
from __future__ import annotations

import struct
from pathlib import Path


class _GPAKEntry:
    __slots__ = ("path", "length", "offset")

    def __init__(self, path: str, length: int, offset: int = 0) -> None:
        self.path = path
        self.length = length
        self.offset = offset


class GPAK:
    """Read a GPAK archive (same binary format as ShootMe/GPAK-Extractor)."""

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = str(file_path)
        self.entries: list[_GPAKEntry] = []
        self.count: int = 0
        self._read_file()

    # ------------------------------------------------------------------
    def _read_file(self) -> None:
        with open(self.file_path, "rb") as f:
            (self.count,) = struct.unpack("<i", f.read(4))
            for _ in range(self.count):
                (text_len,) = struct.unpack("<h", f.read(2))
                path = f.read(text_len).decode("utf-8", errors="replace")
                (length,) = struct.unpack("<i", f.read(4))
                self.entries.append(_GPAKEntry(path, length))
            # data block starts immediately after the directory header
            data_start = f.tell()

        # assign absolute file offsets (same logic as C# ReadFile)
        position = data_start
        for entry in self.entries:
            entry.offset = position
            position += entry.length

    # ------------------------------------------------------------------
    def read_entry(self, target_path: str) -> bytes | None:
        """Return raw bytes for *target_path*, or None if not found.

        Matching normalises path separators so "swfs/catparts.swf" and
        "swfs\\catparts.swf" are treated as equivalent.
        """
        needle = target_path.replace("\\", "/")
        for entry in self.entries:
            if entry.path.replace("\\", "/") == needle:
                with open(self.file_path, "rb") as f:
                    f.seek(entry.offset)
                    return f.read(entry.length)
        return None

    def list_entries(self) -> list[str]:
        """Return a list of all entry paths in the archive."""
        return [e.path for e in self.entries]


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def extract_entry(gpak_path: str | Path, entry_path: str) -> bytes | None:
    """Extract a single entry from a GPAK archive.

    Args:
        gpak_path:  Path to the .gpak file.
        entry_path: Internal archive path, e.g. ``"swfs/catparts.swf"``.

    Returns:
        Raw bytes of the entry, or ``None`` on error / not found.
    """
    try:
        return GPAK(gpak_path).read_entry(entry_path)
    except Exception:
        return None
