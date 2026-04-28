from __future__ import annotations

from pathlib import Path

from PIL import Image


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=str)
    args = parser.parse_args()

    image_path = Path(args.image)
    img = Image.open(image_path)
    exif = img.getexif()

    print("path:", image_path)
    print("size:", img.size)
    print("mode:", img.mode)
    print("has_exif:", bool(exif))
    if exif:
        print("exif_orientation(274):", exif.get(274))


if __name__ == "__main__":
    main()
