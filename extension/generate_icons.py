#!/usr/bin/env python3
"""Generate placeholder icons for the extension."""
import os
try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow not installed. Skipping icon generation.")
    exit(0)

def create_icon(size, path):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Simple "W" logo
    margin = max(2, size // 8)
    w = size - 2 * margin
    h = size - 2 * margin
    # Draw a download arrow shape
    points = [
        (margin + w * 0.5, margin),           # top center
        (margin + w, margin + h * 0.6),       # right
        (margin + w * 0.7, margin + h * 0.6), # right indent
        (margin + w * 0.7, margin + h),        # bottom right
        (margin + w * 0.3, margin + h),        # bottom left
        (margin + w * 0.3, margin + h * 0.6), # left indent
        (margin, margin + h * 0.6),            # left
    ]
    draw.polygon(points, fill=(25, 118, 210, 255))  # Blue
    img.save(path)
    print(f"Created {path}")

os.makedirs("icons", exist_ok=True)
create_icon(16, "icons/icon16.png")
create_icon(48, "icons/icon48.png")
create_icon(128, "icons/icon128.png")
print("Icons generated!")
