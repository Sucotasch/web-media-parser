# -*- coding: utf-8 -*-
"""R-P verification: queue save() on a directory-less path, and _sanitize_filename."""
import os
import re
import sys
import tempfile
import pathlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.getcwd())

from src.core.task_queue_manager import TaskQueueManager
from src.parser.parser_manager import ParserManager

print("### R-P-1: TaskQueueManager.save() with a path that has no directory")
q = TaskQueueManager(tempfile.mkdtemp(prefix="q_"))
for path in ["task_queue.json", os.path.join(tempfile.gettempdir(), "sub_x", "q.json")]:
    try:
        q.save(path)
        print(f"  save({path!r}) -> ok")
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"  save({path!r}) -> {type(e).__name__}: {e}")

print("\n  proposed fix:")
for path in ["task_queue.json", os.path.join(tempfile.gettempdir(), "sub_x", "q.json")]:
    d = os.path.dirname(path)
    try:
        if d:
            os.makedirs(d, exist_ok=True)
        print(f"  save({path!r}) -> ok (dir={'created/skipped' if d else 'none needed'})")
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"  save({path!r}) -> {type(e).__name__}: {e}")

print("\n### R-P-2: _sanitize_filename on realistic names")
send = ParserManager.__dict__["_sanitize_filename"]


def proposed(name, media_type="image"):
    invalid = r'<>:"/\\|?*' + "".join(map(chr, range(32)))
    s = re.sub(f"[{re.escape(invalid)}]+", "_", name)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"__+", "_", s).strip("_")
    name_part, ext_part = os.path.splitext(s)
    m = re.match(r"^\.[A-Za-z0-9]{1,5}$", ext_part)
    if m:
        ext_part = m.group(0)
    else:
        # keep a sane default instead of a mangled stub
        from src import constants as K
        ext_part = K.DEFAULT_VIDEO_EXTENSION if media_type == "video" else K.DEFAULT_IMAGE_EXTENSION
    if len(name_part) > 200:
        name_part = name_part[:200]
    if not name_part:
        import hashlib
        name_part = hashlib.md5(name.encode()).hexdigest()[:10]
    return f"{name_part}{ext_part}"


def proposed2(name):
    """Surgical variant: recover the real extension instead of a mangled stub."""
    from src import constants as K
    invalid = r'<>:"/\\|?*' + "".join(map(chr, range(32)))
    s = re.sub(f"[{re.escape(invalid)}]+", "_", name)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"__+", "_", s).strip("_")
    name_part, ext_part = os.path.splitext(s)
    known = sorted(K.IMAGE_EXTENSIONS + K.VIDEO_EXTENSIONS + K.AUDIO_EXTENSIONS,
                   key=len, reverse=True)
    low = ext_part.lower()
    match = next((e for e in known if low.startswith(e)), None)
    if match:
        ext_part = ext_part[:len(match)]
    elif len(ext_part) > 7:
        ext_part = ext_part[:7]          # unchanged legacy fallback
    if len(name_part) > K.MAX_FILENAME_LENGTH:
        name_part = name_part[:K.MAX_FILENAME_LENGTH]
    if not name_part:
        import hashlib
        name_part = hashlib.md5(name.encode()).hexdigest()[:K.DEFAULT_FILENAME_HASH_LENGTH]
    return f"{name_part}{ext_part}"


CASES = [
    "photo.jpeg?w=100", "pic.avif?x=1&y=2", "image.webp", "clip.mp4",
    "shot.png", "a.jpeg_small", "video.m3u8", "wallpaper.avif", "noext",
    "img.jpeg?resize=1000,2000", "photo.JPEG", "x.tar.gz", "photo.jpeg;param=1",
]
print(f"{'input':30} {'now':26} {'proposed':22} verdict")
bad = 0
for c in CASES:
    try:
        now = send(None, c)
    except Exception as e:
        now = f"<{type(e).__name__}>"
    new = proposed(c)
    # a mangled extension = the tail after the last dot is not a known media ext
    known = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".svg",
             ".mp4", ".webm", ".mov", ".m3u8", ".mpd", ".mp3", ".wav", ".ogg")
    now_ok = os.path.splitext(now)[1].lower() in known
    new_ok = os.path.splitext(new)[1].lower() in known
    if not now_ok:
        bad += 1
    p2 = proposed2(c)
    p2_ok = os.path.splitext(p2)[1].lower() in known
    print(f"{c:30} {now:26} {new:22} {p2:22} now={'ok' if now_ok else 'BAD'} "
          f"new={'ok' if new_ok else 'BAD'} new2={'ok' if p2_ok else 'BAD'}")
    print(f"{'':30} {'':26} {'':22} {'<= proposed2':22} -> {p2}" if p2 != new else "")
print(f"\n  inputs whose CURRENT result has an unusable extension: {bad}/{len(CASES)}")
