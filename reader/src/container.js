// Parser for the .mvpdf binary container (see compiler/mvpdf/container.py for the layout).
// Pure logic, no DOM: runs in browsers and in Node tests.

export const MAGIC = Uint8Array.from([0x89, 0x4d, 0x56, 0x50, 0x44, 0x46, 0x0d, 0x0a, 0x1a, 0x0a]);
export const VERSION_MAJOR = 2;
export const FLAG_DEFLATE = 0x01;
const HEADER_BYTES = 18; // magic(10) u16 major, u16 minor, u32 count
const BLOCK_HEADER_BYTES = 36; // kind(4) flags(1) pad(3) id(16) stored(4) raw(4) crc(4)
const ID_BYTES = 16;
const MAX_BLOCK_BYTES = 512 * 1024 * 1024;

export class MvpdfError extends Error {
  constructor(message) {
    super(message);
    this.name = "MvpdfError";
  }
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(bytes) {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const ascii = (bytes) => String.fromCharCode(...bytes);

/** Inflate zlib data, refusing to produce more than `rawLength` bytes (decompression bombs). */
export async function inflateBounded(bytes, rawLength) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
  const reader = stream.getReader();
  const out = new Uint8Array(rawLength);
  let offset = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (offset + value.length > rawLength) {
      await reader.cancel();
      throw new MvpdfError("A block inflates to more data than it declares");
    }
    out.set(value, offset);
    offset += value.length;
  }
  if (offset !== rawLength) throw new MvpdfError("A block inflates to the wrong size");
  return out;
}

function readHeader(view, bytes) {
  if (bytes.length < HEADER_BYTES) throw new MvpdfError("File is too short to be an .mvpdf");
  if (!MAGIC.every((b, i) => bytes[i] === b)) {
    throw new MvpdfError("Not an .mvpdf file. Convert PDFs first with: python -m mvpdf file.pdf");
  }
  const major = view.getUint16(10, true);
  if (major !== VERSION_MAJOR) {
    throw new MvpdfError(`Unsupported .mvpdf version ${major}; this reader supports ${VERSION_MAJOR}`);
  }
  return view.getUint32(14, true);
}

function readBlockHeader(view, bytes, offset) {
  return {
    kind: ascii(bytes.subarray(offset, offset + 4)),
    flags: view.getUint8(offset + 4),
    id: toHex(bytes.subarray(offset + 8, offset + 8 + ID_BYTES)),
    storedLength: view.getUint32(offset + 24, true),
    rawLength: view.getUint32(offset + 28, true),
    crc: view.getUint32(offset + 32, true),
  };
}

/**
 * Parse and verify a container.
 * @returns {Promise<Map<string, {kind: string, data: Uint8Array}>>} blocks keyed by id
 */
export async function readContainer(buffer, inflate = inflateBounded) {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const count = readHeader(view, bytes);
  const blocks = new Map();
  let offset = HEADER_BYTES;
  for (let index = 0; index < count; index += 1) {
    if (offset + BLOCK_HEADER_BYTES > bytes.length) {
      throw new MvpdfError(`File is truncated (block ${index} header)`);
    }
    const head = readBlockHeader(view, bytes, offset);
    offset += BLOCK_HEADER_BYTES;
    if (head.rawLength > MAX_BLOCK_BYTES || offset + head.storedLength > bytes.length) {
      throw new MvpdfError(`File is truncated or corrupt (${head.kind} block)`);
    }
    const payload = bytes.subarray(offset, offset + head.storedLength);
    offset += head.storedLength;
    if (crc32(payload) !== head.crc) {
      throw new MvpdfError(`Checksum mismatch in ${head.kind} block: the file is corrupt`);
    }
    const data = head.flags & FLAG_DEFLATE ? await inflate(payload, head.rawLength) : payload;
    blocks.set(head.id, Object.freeze({ kind: head.kind, data }));
  }
  return blocks;
}
