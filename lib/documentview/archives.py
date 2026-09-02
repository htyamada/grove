"""Bounded, streamed access to untrusted EPUB/CBZ ZIP archives.

Primary enforcement is on the streamed decompressed output, since that is
the only thing Python's `zipfile` actually lets a caller observe live. One
`SafeZipReader` owns a cumulative decompressed-byte counter for the whole
cover-or-preview operation it backs; every member read through it
contributes to that same total, so opening members separately cannot reset
or bypass `DOCUMENT_VIEWER_MAX_TOTAL_BYTES`. `ZipInfo.compress_size` /
`ZipInfo.file_size` are used only as a cheap declared-ratio pre-check
against `DOCUMENT_VIEWER_MAX_COMPRESSION_RATIO` before opening an entry at
all -- a fast rejection for an obviously hostile declared ratio, not a live
streamed counter (`zipfile` doesn't expose one).
"""
import zipfile
from pathlib import PurePosixPath

from . import config


class ArchiveError(Exception):
    pass


class ArchiveTooLargeError(ArchiveError):
    pass


class ArchiveBombError(ArchiveError):
    pass


class ArchiveEncryptedError(ArchiveError):
    pass


def _is_safe_member_name(name: str) -> bool:
    if not name or '\x00' in name:
        return False
    pure = PurePosixPath(name)
    if pure.is_absolute():
        return False
    return '..' not in pure.parts


class SafeZipReader:
    def __init__(self, fileobj):
        try:
            self._zf = zipfile.ZipFile(fileobj)
        except zipfile.BadZipFile as e:
            raise ArchiveError(f'not a valid zip archive: {e}') from e

        self._total_read = 0
        self._max_entries = config.limit('DOCUMENT_VIEWER_MAX_ARCHIVE_ENTRIES')
        self._max_entry_bytes = config.limit('DOCUMENT_VIEWER_MAX_ENTRY_BYTES')
        self._max_total_bytes = config.limit('DOCUMENT_VIEWER_MAX_TOTAL_BYTES')
        self._max_ratio = config.limit('DOCUMENT_VIEWER_MAX_COMPRESSION_RATIO')

        infolist = self._zf.infolist()
        if len(infolist) > self._max_entries:
            raise ArchiveTooLargeError('too many archive entries')

        self._infos = {}
        for info in infolist:
            if info.is_dir():
                continue
            if not _is_safe_member_name(info.filename):
                continue
            if info.flag_bits & 0x1:
                raise ArchiveEncryptedError(f'encrypted archive entry: {info.filename}')
            self._infos[info.filename] = info

    def close(self):
        self._zf.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def names(self):
        return list(self._infos)

    def image_names_natural_order(self, suffixes):
        from .documents import natural_sort_key

        names = [n for n in self._infos if n.lower().endswith(suffixes)]
        return sorted(names, key=natural_sort_key)

    def info(self, name):
        info = self._infos.get(name)
        if info is None:
            raise ArchiveError(f'no such archive member: {name}')
        return info

    def read(self, name: str, chunk_size: int = 65536, max_len: 'int | None' = None) -> bytes:
        """Read the full member, or stop early once `max_len` bytes have
        been read (for a bounded preview excerpt) -- still subject to the
        entry/total byte caps below regardless of `max_len`.
        """
        info = self.info(name)

        if info.compress_size and info.file_size:
            ratio = info.file_size / info.compress_size
            if ratio > self._max_ratio:
                raise ArchiveBombError(f'declared compression ratio too high: {name}')
        if info.file_size > self._max_entry_bytes:
            raise ArchiveTooLargeError(f'archive member too large: {name}')

        chunks = []
        entry_read = 0
        with self._zf.open(info) as fh:
            while max_len is None or entry_read < max_len:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                entry_read += len(chunk)
                self._total_read += len(chunk)
                if entry_read > self._max_entry_bytes:
                    raise ArchiveTooLargeError(f'archive member too large: {name}')
                if self._total_read > self._max_total_bytes:
                    raise ArchiveTooLargeError('archive operation exceeded total byte budget')
                chunks.append(chunk)
        data = b''.join(chunks)
        return data[:max_len] if max_len is not None else data

    def read_text_xml(self, name: str) -> bytes:
        max_xml = config.limit('DOCUMENT_VIEWER_MAX_XML_BYTES')
        info = self.info(name)
        if info.file_size > max_xml:
            raise ArchiveTooLargeError(f'XML member too large: {name}')
        data = self.read(name)
        if len(data) > max_xml:
            raise ArchiveTooLargeError(f'XML member too large: {name}')
        return data
