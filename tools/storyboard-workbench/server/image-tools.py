"""Technical image operations only; no visual or creative scoring."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 50_000_000


def verify(file, expected=None):
    with Image.open(file) as image:
        if image.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("Only PNG, JPEG and WebP images are supported")
        image.verify()
    with Image.open(file) as image:
        image.load()
        width, height = ImageOps.exif_transpose(image).size
        if min(width, height) < 64:
            raise ValueError("Image dimensions are too small")
        if expected:
            ratio = expected[0] / expected[1]
            if abs(width / height / ratio - 1) > 0.025:
                raise ValueError(f"Returned dimensions {width}x{height} do not match requested aspect ratio")
        return {"width": width, "height": height, "format": image.format, "bytes": os.path.getsize(file)}


def main(task):
    source = task["file"]
    if task["action"] == "verify":
        return verify(source, task.get("expected"))
    output = task["output"]
    if Path(output).exists():
        raise ValueError("Versioned output already exists")
    verify(source, task.get("expected"))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    if task["action"] == "thumbnail":
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((480, 480), Image.Resampling.LANCZOS)
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            with open(output, "xb") as target:
                image.save(target, format="WEBP", quality=82)
            return {"width": image.width, "height": image.height, "format": "WEBP", "bytes": os.path.getsize(output)}
    elif task["action"] == "normalize":
        with Image.open(source) as image:
            image.load()
            profile = image.info.get("icc_profile")
            image = ImageOps.exif_transpose(image)
            # Preserve pixel appearance, including alpha. This only normalizes the container.
            if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                image = image.convert("RGB")
            with open(output, "xb") as target:
                image.save(target, format="PNG", icc_profile=profile)
    elif task["action"] == "grid":
        with tempfile.TemporaryDirectory(prefix="grid-", dir=Path(output).parent) as folder:
            frames = []
            for index, yaw in enumerate((0, 90, 180, -90)):
                frame = str(Path(folder) / f"{index}.png")
                subprocess.run([task["ffmpeg"], "-hide_banner", "-loglevel", "error", "-nostdin", "-i", source,
                                "-vf", f"v360=input=e:output=flat:yaw={yaw}:pitch=0:h_fov=95:v_fov=70,scale=768:512",
                                "-frames:v", "1", frame], check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                frames.append(frame)
            canvas = Image.new("RGB", (1536, 1024))
            for index, frame in enumerate(frames):
                with Image.open(frame) as image:
                    canvas.paste(image, ((index % 2) * 768, (index // 2) * 512))
            with open(output, "xb") as target:
                canvas.save(target, format="PNG")
    else:
        raise ValueError("Unsupported operation")
    return verify(output, task.get("expected"))


if __name__ == "__main__":
    print(json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
