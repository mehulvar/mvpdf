"""The .mvpdf binary container: a flat sequence of typed, content-addressed blocks.

Layout (all integers little-endian)::

    header   MAGIC (10 bytes) | u16 major | u16 minor | u32 block_count
    block*   type (4 ASCII) | u8 flags | 3 pad | id (16) | u32 stored_len | u32 raw_len | u32 crc32
             payload (stored_len bytes; deflated when flags & FLAG_DEFLATE)

Unlike PDF (text object syntax + a cross-reference table at the end of the file),
every block carries its own length and checksum, and its id is a hash of its content,
so identical resources (an image used on 40 pages) are stored exactly once.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from typing import Iterable

MAGIC = b"\x89MVPDF\r\n\x1a\n"  # high bit + CRLF + ^Z catch text-mode/7-bit corruption
VERSION_MAJOR = 2
VERSION_MINOR = 0
FLAG_DEFLATE = 0x01
ID_BYTES = 16
MAX_BLOCK_BYTES = 512 * 1024 * 1024
MIN_SAVING_RATIO = 0.95  # only keep deflated output if it saves at least 5%

_HEADER = struct.Struct("<10sHHI")
_BLOCK = struct.Struct(f"<4sB3x{ID_BYTES}sIII")


class ContainerError(ValueError):
    """The bytes are not a valid .mvpdf container."""


@dataclass(frozen=True)
class Block:
    kind: str
    data: bytes

    @property
    def id(self) -> str:
        return block_id(self.kind, self.data)


def block_id(kind: str, data: bytes) -> str:
    """Content address: identical (kind, data) pairs always get the same id."""
    return hashlib.blake2b(kind.encode("ascii") + data, digest_size=ID_BYTES).hexdigest()


def _validate_kind(kind: str) -> bytes:
    encoded = kind.encode("ascii")
    if len(encoded) != 4 or not encoded.isalnum():
        raise ContainerError(f"Block type must be 4 alphanumeric ASCII chars, got {kind!r}")
    return encoded


def _encode_block(block: Block, compress: bool) -> bytes:
    kind = _validate_kind(block.kind)
    if len(block.data) > MAX_BLOCK_BYTES:
        raise ContainerError(f"{block.kind} block exceeds {MAX_BLOCK_BYTES} bytes")
    payload, flags = block.data, 0
    if compress:
        deflated = zlib.compress(block.data, level=9)
        if len(deflated) < len(block.data) * MIN_SAVING_RATIO:
            payload, flags = deflated, FLAG_DEFLATE
    header = _BLOCK.pack(
        kind, flags, bytes.fromhex(block.id), len(payload), len(block.data), zlib.crc32(payload)
    )
    return header + payload


def pack(blocks: Iterable[Block], incompressible: frozenset[str] = frozenset()) -> bytes:
    """Serialize blocks, dropping exact duplicates. Kinds in `incompressible` skip deflate."""
    unique: dict[str, Block] = {}
    for block in blocks:
        unique.setdefault(block.id, block)
    body = b"".join(_encode_block(b, b.kind not in incompressible) for b in unique.values())
    return _HEADER.pack(MAGIC, VERSION_MAJOR, VERSION_MINOR, len(unique)) + body


def _read_header(data: bytes) -> int:
    if len(data) < _HEADER.size:
        raise ContainerError("File is too short to be an .mvpdf")
    magic, major, _minor, count = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ContainerError("Not an .mvpdf file (bad magic bytes)")
    if major != VERSION_MAJOR:
        raise ContainerError(f"Unsupported .mvpdf version {major}; expected {VERSION_MAJOR}")
    return count


def _decode_payload(kind: str, flags: int, payload: bytes, raw_len: int) -> bytes:
    if not flags & FLAG_DEFLATE:
        return payload
    inflater = zlib.decompressobj()
    data = inflater.decompress(payload, raw_len + 1)  # bounded: refuses decompression bombs
    if len(data) != raw_len or inflater.unconsumed_tail:
        raise ContainerError(f"{kind} block inflates to the wrong size")
    return data


def unpack(data: bytes) -> dict[str, Block]:
    """Parse and verify a container. Returns blocks keyed by id."""
    count = _read_header(data)
    offset, blocks = _HEADER.size, {}
    for index in range(count):
        if offset + _BLOCK.size > len(data):
            raise ContainerError(f"File is truncated (block {index} header)")
        raw_kind, flags, raw_id, stored_len, raw_len, crc = _BLOCK.unpack_from(data, offset)
        offset += _BLOCK.size
        kind = raw_kind.decode("ascii", errors="replace")
        if raw_len > MAX_BLOCK_BYTES or offset + stored_len > len(data):
            raise ContainerError(f"File is truncated or corrupt ({kind} block)")
        payload = data[offset : offset + stored_len]
        offset += stored_len
        if zlib.crc32(payload) != crc:
            raise ContainerError(f"Checksum mismatch in {kind} block: file is corrupt")
        block = Block(kind, _decode_payload(kind, flags, payload, raw_len))
        if block.id != raw_id.hex():
            raise ContainerError(f"{kind} block content does not match its id")
        blocks[block.id] = block
    return blocks
