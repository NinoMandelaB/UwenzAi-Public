// src/utils/photoUpload.js
//
// Client-side photo-upload guardrails for the chat composer: the byte-size
// and pixel limits are checked BEFORE the upload so the user gets an
// immediate, precise warning ("maximum 10 MB") instead of a failed send.
//
// The backend remains the source of truth (a crafted client can skip these
// checks); this is UX, not enforcement. Pixel dimensions are read from the
// image HEADER only — the first bytes of the file — so a decompression-bomb
// image is never decoded in the browser either.

// Server defaults; /me (auth/me) carries the live values per deployment.
export const DEFAULT_PHOTO_MAX_BYTES = 10 * 1024 * 1024;
export const DEFAULT_PHOTO_MAX_MEGAPIXELS = 25;

/**
 * Read the pixel dimensions of a JPEG/PNG/WebP file from its header.
 * Reads at most the first 128 KiB and never decodes pixel data.
 * Returns { width, height } or null when the header cannot be parsed
 * (the backend check still catches those files).
 */
export async function readImageDimensions(file) {
  if (!(file instanceof File) || file.size < 16) return null;
  try {
    const head = new Uint8Array(await file.slice(0, 131072).arrayBuffer());
    return parseDimensions(head);
  } catch {
    return null;
  }
}

function parseDimensions(bytes) {
  // PNG: 8-byte signature, then IHDR: width (4, BE) and height (4, BE).
  if (
    bytes.length > 24 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47
  ) {
    return {
      width: view32(bytes, 16),
      height: view32(bytes, 20),
    };
  }

  // JPEG: walk the marker segments to the first SOF frame.
  if (bytes.length > 4 && bytes[0] === 0xff && bytes[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < bytes.length) {
      if (bytes[offset] !== 0xff) return null;
      const marker = bytes[offset + 1];
      // SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15 carry the frame
      // header: length (2), precision (1), height (2), width (2).
      const isSof =
        (marker >= 0xc0 && marker <= 0xc3) ||
        (marker >= 0xc5 && marker <= 0xc7) ||
        (marker >= 0xc9 && marker <= 0xcb) ||
        (marker >= 0xcd && marker <= 0xcf);
      if (isSof) {
        return {
          height: view16(bytes, offset + 5),
          width: view16(bytes, offset + 7),
        };
      }
      if (
        marker === 0xd8 ||
        marker === 0x01 ||
        (marker >= 0xd0 && marker <= 0xd7)
      ) {
        offset += 2; // standalone marker
        continue;
      }
      offset += 2 + view16(bytes, offset + 2); // segment with a length
    }
    return null;
  }

  // WebP: RIFF container; dimensions live in the VP8/VP8L/VP8X chunk.
  if (
    bytes.length > 30 &&
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  ) {
    const chunk = String.fromCharCode(
      bytes[12],
      bytes[13],
      bytes[14],
      bytes[15],
    );
    if (chunk === "VP8X") {
      // Canvas: 24-bit width-1 / height-1 at offsets 24 and 27.
      return {
        width: 1 + (bytes[24] | (bytes[25] << 8) | (bytes[26] << 16)),
        height: 1 + (bytes[27] | (bytes[28] << 8) | (bytes[29] << 16)),
      };
    }
    if (chunk === "VP8 ") {
      // Lossy: frame tag (3), sync code (3), then 16-bit width/height.
      return {
        width: view16(bytes, 26) & 0x3fff,
        height: view16(bytes, 28) & 0x3fff,
      };
    }
    if (chunk === "VP8L") {
      // Lossless: signature byte, then 14-bit width-1 / height-1.
      const bits =
        bytes[21] | (bytes[22] << 8) | (bytes[23] << 16) | (bytes[24] << 24);
      return {
        width: 1 + (bits & 0x3fff),
        height: 1 + ((bits >> 14) & 0x3fff),
      };
    }
    return null;
  }

  return null;
}

function view16(bytes, offset) {
  return bytes[offset] | (bytes[offset + 1] << 8);
}

function view32(bytes, offset) {
  return (
    (bytes[offset] << 24) |
    (bytes[offset + 1] << 16) |
    (bytes[offset + 2] << 8) |
    bytes[offset + 3]
  );
}

function isValidResult(dimension) {
  return (
    dimension &&
    Number.isInteger(dimension.width) &&
    Number.isInteger(dimension.height) &&
    dimension.width > 0 &&
    dimension.height > 0
  );
}

/**
 * Validate a picked photo against the server limits, before any upload.
 *
 * Returns { ok: true, dimensions } or
 * { ok: false, code: "photo_too_large" | "photo_dimensions", max } where
 * `max` is the human-readable limit for the localized warning message.
 * A header that cannot be parsed is not an error here: the backend makes
 * the final call.
 */
export async function validatePhotoFile(file, limits = {}) {
  if (!(file instanceof File)) return { ok: false, code: "not_a_file" };
  const maxBytes = limits.maxBytes || DEFAULT_PHOTO_MAX_BYTES;
  const maxMegapixels = limits.maxMegapixels || DEFAULT_PHOTO_MAX_MEGAPIXELS;

  if (file.size > maxBytes) {
    return {
      ok: false,
      code: "photo_too_large",
      max: Math.round(maxBytes / (1024 * 1024)),
    };
  }

  const dimensions = await readImageDimensions(file);
  if (isValidResult(dimensions)) {
    const megapixels = (dimensions.width * dimensions.height) / 1_000_000;
    if (megapixels > maxMegapixels) {
      return { ok: false, code: "photo_dimensions", max: maxMegapixels };
    }
  }

  return { ok: true, dimensions };
}
