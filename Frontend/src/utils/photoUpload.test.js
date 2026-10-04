import { describe, expect, it } from "vitest";

import {
  DEFAULT_PHOTO_MAX_MEGAPIXELS,
  readImageDimensions,
  validatePhotoFile,
} from "./photoUpload";

// Minimal real image headers built from tiny valid files, with the
// dimension fields patched — mirrors how the backend tests build
// decompression-bomb-shaped inputs without decoding huge canvases.
function pngWithDimensions(width, height) {
  const bytes = new Uint8Array(33);
  const signature = [
    0x89,
    0x50,
    0x4e,
    0x47,
    0x0d,
    0x0a,
    0x1a,
    0x0a, // PNG signature
    0x00,
    0x00,
    0x00,
    0x0d, // IHDR length
    0x49,
    0x48,
    0x44,
    0x52, // "IHDR"
  ];
  bytes.set(signature, 0);
  bytes[16] = (width >>> 24) & 0xff;
  bytes[17] = (width >>> 16) & 0xff;
  bytes[18] = (width >>> 8) & 0xff;
  bytes[19] = width & 0xff;
  bytes[20] = (height >>> 24) & 0xff;
  bytes[21] = (height >>> 16) & 0xff;
  bytes[22] = (height >>> 8) & 0xff;
  bytes[23] = height & 0xff;
  // bit depth / color type / compression / filter / interlace + CRC
  bytes.fill(0, 24, 29);
  bytes.set([0x98, 0x76, 0x54, 0x32], 29);
  return bytes;
}

function fileFromBytes(bytes, type = "image/png") {
  return new File([bytes], "photo.png", { type });
}

describe("readImageDimensions", () => {
  it("reads PNG dimensions from the header", async () => {
    const dimensions = await readImageDimensions(
      fileFromBytes(pngWithDimensions(640, 480)),
    );
    expect(dimensions).toEqual({ width: 640, height: 480 });
  });

  it("returns null for a non-image header", async () => {
    const file = new File([new Uint8Array(64).fill(1)], "x.bin", {
      type: "application/octet-stream",
    });
    expect(await readImageDimensions(file)).toBeNull();
  });

  it("returns null for non-file input", async () => {
    expect(await readImageDimensions(null)).toBeNull();
  });
});

describe("validatePhotoFile", () => {
  it("accepts a photo within both limits", async () => {
    const result = await validatePhotoFile(
      fileFromBytes(pngWithDimensions(64, 48)),
    );
    expect(result.ok).toBe(true);
    expect(result.dimensions).toEqual({ width: 64, height: 48 });
  });

  it("rejects a file above the byte limit with the max in MB", async () => {
    const small = fileFromBytes(pngWithDimensions(64, 48));
    Object.defineProperty(small, "size", { value: 11 * 1024 * 1024 });
    const result = await validatePhotoFile(small, {
      maxBytes: 10 * 1024 * 1024,
    });
    expect(result).toEqual({ ok: false, code: "photo_too_large", max: 10 });
  });

  it("rejects a header declaring too many megapixels", async () => {
    // 9000 x 9000 = 81 Mpx > default 25 Mpx, tiny header.
    const bomb = fileFromBytes(pngWithDimensions(9000, 9000));
    const result = await validatePhotoFile(bomb);
    expect(result).toEqual({
      ok: false,
      code: "photo_dimensions",
      max: DEFAULT_PHOTO_MAX_MEGAPIXELS,
    });
  });

  it("does not reject when the header cannot be parsed (backend decides)", async () => {
    const unknown = new File([new Uint8Array(2048).fill(0x41)], "x.png", {
      type: "image/png",
    });
    const result = await validatePhotoFile(unknown);
    expect(result.ok).toBe(true);
    expect(result.dimensions).toBeNull();
  });

  it("honours a custom megapixel limit", async () => {
    const result = await validatePhotoFile(
      fileFromBytes(pngWithDimensions(4000, 4000)),
      {
        maxMegapixels: 10, // 16 Mpx exceeds it
      },
    );
    expect(result.ok).toBe(false);
    expect(result.code).toBe("photo_dimensions");
    expect(result.max).toBe(10);
  });
});
