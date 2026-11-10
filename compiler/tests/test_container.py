import struct
import zlib

import pytest

from mvpdf.container import MAGIC, Block, ContainerError, block_id, pack, unpack

HEADER = struct.Struct("<10sHHI")
BLOCK = struct.Struct("<4sB3x16sIII")


def test_round_trip_preserves_every_block():
    blocks = [Block("META", b'{"a":1}'), Block("IMAG", b"\x89PNG..."), Block("SVGG", b"<svg/>" * 50)]

    restored = unpack(pack(blocks, incompressible=frozenset({"IMAG"})))

    assert {b.id: b for b in blocks} == restored


def test_identical_blocks_are_stored_once():
    image = Block("IMAG", b"same bytes" * 100)

    data = pack([image, image, Block("IMAG", b"same bytes" * 100)])

    assert HEADER.unpack_from(data)[3] == 1
    assert len(unpack(data)) == 1


def test_ids_are_content_addresses():
    assert block_id("PAGE", b"x") == Block("PAGE", b"x").id
    assert block_id("PAGE", b"x") != block_id("FLOW", b"x")


def test_compressible_blocks_are_deflated():
    data = pack([Block("SVGG", b"a" * 10_000)])
    _kind, flags, _id, stored, raw, _crc = BLOCK.unpack_from(data, HEADER.size)
    assert flags == 1 and stored < raw == 10_000


@pytest.mark.parametrize("kind", ["TOOLONG", "AB", "a-b!"])
def test_rejects_bad_block_kinds(kind):
    with pytest.raises(ContainerError):
        pack([Block(kind, b"x")])


def test_rejects_bad_magic():
    with pytest.raises(ContainerError, match="magic"):
        unpack(b"%PDF-1.7" + b"\0" * 20)


def test_rejects_short_file():
    with pytest.raises(ContainerError, match="too short"):
        unpack(MAGIC)


def test_rejects_unknown_major_version():
    with pytest.raises(ContainerError, match="version 9"):
        unpack(HEADER.pack(MAGIC, 9, 0, 0))


def test_detects_corruption_with_checksum():
    data = bytearray(pack([Block("META", b'{"title":"hello"}')]))
    data[-3] ^= 0xFF
    with pytest.raises(ContainerError, match="Checksum"):
        unpack(bytes(data))


def test_detects_truncation():
    data = pack([Block("META", b"x" * 100), Block("FLOW", b"y" * 100)])
    with pytest.raises(ContainerError, match="truncated"):
        unpack(data[:-10])


def test_refuses_blocks_that_inflate_beyond_their_declared_size():
    bomb = zlib.compress(b"\0" * 100_000)
    raw = b"x" * 10
    header = BLOCK.pack(b"SVGG", 1, bytes.fromhex(block_id("SVGG", raw)), len(bomb), 10, zlib.crc32(bomb))
    with pytest.raises(ContainerError, match="wrong size"):
        unpack(HEADER.pack(MAGIC, 2, 0, 1) + header + bomb)


def test_detects_payload_that_does_not_match_its_id():
    payload = b"tampered"
    header = BLOCK.pack(b"META", 0, b"\0" * 16, len(payload), len(payload), zlib.crc32(payload))
    with pytest.raises(ContainerError, match="does not match its id"):
        unpack(HEADER.pack(MAGIC, 2, 0, 1) + header + payload)
