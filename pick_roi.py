# -*- coding: utf-8 -*-
"""
ROI picker (Windows)
- マウス位置を2点(左上/右下)で指定して region.json を保存
- ついでに out/roi_preview.png を作ってプレビュー確認できる
"""

import json
import time
import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
import mss


BASE_DIR = Path(__file__).parent
OUT_DIR = BASE_DIR / "out"
OUT_DIR.mkdir(exist_ok=True)

REGION_FILE = OUT_DIR / "region.json"
PREVIEW_FILE = OUT_DIR / "roi_preview.png"


# --- mouse position via WinAPI
class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def get_cursor_pos() -> tuple[int, int]:
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def wait_enter(msg: str) -> tuple[int, int]:
    print(msg)
    input("Enter to capture current mouse position...")
    xy = get_cursor_pos()
    print(f"  captured: {xy}")
    return xy


def clamp_region(region: dict, monitor: dict) -> dict:
    # monitor has: left, top, width, height
    ml, mt = int(monitor["left"]), int(monitor["top"])
    mw, mh = int(monitor["width"]), int(monitor["height"])

    left = max(ml, min(region["left"], ml + mw - 1))
    top = max(mt, min(region["top"], mt + mh - 1))

    right = max(left + 1, min(region["left"] + region["width"], ml + mw))
    bottom = max(top + 1, min(region["top"] + region["height"], mt + mh))

    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", type=int, default=1, help="mss monitor index (default: 1)")
    ap.add_argument("--no-show", action="store_true", help="do not show preview window")
    args = ap.parse_args()

    print("=== ROI picker (2-point) ===")
    print("Tip: Chromeを最大化してから実施すると安定します。")
    print("Tip: 右側が白くなる場合は『右下』がChrome外に出ています。右下をESXi画面内で取り直してください。")
    print("")

    with mss.mss() as sct:
        if args.monitor < 1 or args.monitor >= len(sct.monitors):
            print(f"Invalid monitor index: {args.monitor}. Available: 1..{len(sct.monitors)-1}")
            return

        mon = sct.monitors[args.monitor]

        tl = wait_enter("1) Chromeのブックマークバー直下の『ESXi画面領域の左上』にマウスを合わせる")
        br = wait_enter("2) 『ESXi画面領域の右下』にマウスを合わせる")

        left = min(tl[0], br[0])
        top = min(tl[1], br[1])
        right = max(tl[0], br[0])
        bottom = max(tl[1], br[1])

        region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        region = clamp_region(region, mon)

        REGION_FILE.write_text(json.dumps(region, ensure_ascii=False, indent=2), encoding="utf-8")
        print("")
        print("Saved:", str(REGION_FILE))
        print(region)

        # capture preview
        shot = np.array(sct.grab(region))
        bgr = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        cv2.imwrite(str(PREVIEW_FILE), bgr)
        print("Preview saved:", str(PREVIEW_FILE))

        if not args.no_show:
            cv2.namedWindow("ROI PREVIEW (press any key to close)", cv2.WINDOW_NORMAL)
            cv2.imshow("ROI PREVIEW (press any key to close)", bgr)
            cv2.waitKey(0)
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()