"""Signed identifiers for archive-internal preview subresources (spec 4.4).

An encoded hash/fingerprint/index has no integrity by itself, since a
client can alter any of those fields, and a path hash alone isn't
reversible into the document it names. `documents/preview/<path>/` already
carries the document's collection-relative path as a real, independently
validated URL segment (through `resolve_document()`, same as
`view`/`cover`/`download`), so the subresource id itself never needs to
encode *which document*, only *which member of it*: a small integer index
into a freshly re-parsed, deterministically-ordered listing (EPUB manifest
items, CBZ pages in natural order, PDF page numbers).
"""
from django.core import signing

_SALT = 'documentview.subresource'


class SubresourceError(Exception):
    pass


class StaleSubresourceError(SubresourceError):
    pass


def fingerprint(resolved) -> str:
    return f'{resolved.mtime_ns}:{resolved.size}'


def encode(index: int, resolved) -> str:
    return signing.dumps({'i': index, 'fp': fingerprint(resolved)}, salt=_SALT)


def decode(id_str: str, resolved) -> int:
    try:
        payload = signing.loads(id_str, salt=_SALT, max_age=None)
    except signing.BadSignature as e:
        raise SubresourceError('bad subresource signature') from e

    if not isinstance(payload, dict):
        raise SubresourceError('malformed subresource payload')
    if payload.get('fp') != fingerprint(resolved):
        raise StaleSubresourceError('subresource id no longer matches the current document')

    index = payload.get('i')
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise SubresourceError('invalid subresource index')
    return index
