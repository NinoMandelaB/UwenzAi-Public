export function createImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.addEventListener("load", () => resolve(image));
    image.addEventListener("error", (error) => reject(error));
    image.setAttribute("crossOrigin", "anonymous");
    image.src = url;
  });
}

export async function getCroppedImageBlob(imageSrc, pixelCrop, options = {}) {
  const {
    outputWidth = 800,
    outputHeight = 800,
    mimeType = "image/jpeg",
    quality = 0.9,
  } = options;

  const image = await createImage(imageSrc);
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");

  canvas.width = outputWidth;
  canvas.height = outputHeight;

  ctx.drawImage(
    image,
    pixelCrop.x,
    pixelCrop.y,
    pixelCrop.width,
    pixelCrop.height,
    0,
    0,
    outputWidth,
    outputHeight,
  );

  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error("Failed to crop image."));
          return;
        }
        resolve(blob);
      },
      mimeType,
      quality,
    );
  });
}

export function blobToFile(blob, fileName = "character-avatar.jpg") {
  return new File([blob], fileName, {
    type: blob.type || "image/jpeg",
    lastModified: Date.now(),
  });
}
