# -*- coding: utf-8 -*-
"""
realtime_summary.py
- Capture ROI from ESXi Host Client periodically
- Classify screen state with Gemini asynchronously
- Render overlay for demo
- Save session log and generate markdown summary on quit
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from google import genai
from google.genai import types


# -------------------------
# DPI awareness
# -------------------------

def _set_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_set_dpi_awareness()


# -------------------------
# Config
# -------------------------
BASE_DIR = Path(__file__).parent
OUT_DIR = BASE_DIR / "out"
OUT_DIR.mkdir(exist_ok=True)

REGION_FILE = OUT_DIR / "region.json"
LAST_ROI_FILE = OUT_DIR / "last_roi.jpg"
GEMINI_LOG_FILE = OUT_DIR / "gemini_debug.log"

_gemini_model_raw = os.getenv("GEMINI_MODEL") or os.getenv("GEMINI_VISION_MODEL", "gemini-3-flash-preview")
GEMINI_MODEL = _gemini_model_raw.replace("models/", "", 1)
GEMINI_INTERVAL_SEC = float(os.getenv("GEMINI_INTERVAL_SEC", "0.8"))
GEMINI_MAX_W = int(os.getenv("GEMINI_MAX_W", "1600"))
GEMINI_JPEG_QUALITY = int(os.getenv("GEMINI_JPEG_QUALITY", "88"))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "64"))
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "MINIMAL").upper()
GEMINI_MIN_CONF = float(os.getenv("GEMINI_MIN_CONF", "0.35"))
ENABLE_TEMPLATE_FALLBACK = os.getenv("ENABLE_TEMPLATE_FALLBACK", "1") == "1"
TEMPLATE_MIN_SCORE = float(os.getenv("TEMPLATE_MIN_SCORE", "0.80"))
TEMPLATE_MIN_MARGIN = float(os.getenv("TEMPLATE_MIN_MARGIN", "0.02"))
FAST_SWITCH_STREAK = max(1, int(os.getenv("FAST_SWITCH_STREAK", "2")))
FAST_SWITCH_MIN_CONF = float(os.getenv("FAST_SWITCH_MIN_CONF", "0.85"))
MODAL_PRIORITY_LABELS = {"vm_create", "user_add", "host_reboot"}

VOTE_WINDOW = max(3, int(os.getenv("VOTE_WINDOW", "5")))
HOLD_SEC = float(os.getenv("HOLD_SEC", "0.5"))
DEBUG_GEMINI = os.getenv("DEBUG_GEMINI", "0") == "1"
SAVE_LAST_ROI = os.getenv("SAVE_LAST_ROI", "1") == "1"
SHOW_ROI_WINDOW = os.getenv("SHOW_ROI_WINDOW", "0") == "1"
USE_GEMINI_SUMMARY = os.getenv("USE_GEMINI_SUMMARY", "0") == "1"

WINDOW_TITLE = "ESXi CV Demo (Gemini) q=quit"
ROI_WINDOW_TITLE = "ROI Preview v=toggle"
TEMPLATES_DIR = BASE_DIR / "templates" / "full"

CLASS_LABELS = [
    "login_disconnect",
    "vm_list",
    "storage_list",
    "network_list",
    "vm_create",
    "user_add",
    "license_view",
    "host_reboot",
]
UNKNOWN_LABEL = "unknown"

OVERLAY_MAP = {
    "login_disconnect": "Login/Disconnect",
    "vm_list": "VM list",
    "storage_list": "Storage list",
    "network_list": "Network list",
    "vm_create": "VM create",
    "user_add": "User add dialog",
    "license_view": "License view",
    "host_reboot": "Host reboot",
    UNKNOWN_LABEL: "Unknown",
}

TEMPLATE_FILE_TO_LABEL = {
    "login-disconnect.png": "login_disconnect",
    "vm-list.png": "vm_list",
    "storage-list.png": "storage_list",
    "network-list.png": "network_list",
    "vm-create.png": "vm_create",
    "user-create.png": "user_add",
    "show-license.png": "license_view",
    "reboot-host.png": "host_reboot",
}

OP_KEY_MAP = {
    ord("1"): "manual_vm_create",
    ord("2"): "manual_vm_delete",
    ord("3"): "manual_power_on",
    ord("4"): "manual_power_off",
    ord("5"): "manual_reset",
    ord("6"): "manual_snapshot_create",
    ord("7"): "manual_snapshot_delete",
    ord("8"): "manual_iso_mount",
    ord("9"): "manual_iso_unmount",
}

FONT_PATH_CANDIDATES = [
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
]
FONT_SIZE = 20


# -------------------------
# Utils
# -------------------------

def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_font() -> ImageFont.ImageFont:
    for path in FONT_PATH_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default()


JP_FONT = load_font()


def bgr_to_pil(bgr: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def pil_to_bgr(pil_img: Image.Image) -> np.ndarray:
    rgb = np.array(pil_img)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def draw_lines(bgr: np.ndarray, lines: list[str], x: int = 12, y: int = 12, line_gap: int = 6) -> np.ndarray:
    pil_img = bgr_to_pil(bgr)
    draw = ImageDraw.Draw(pil_img, "RGBA")

    if lines:
        widths = [draw.textlength(s, font=JP_FONT) for s in lines]
        box_w = int(max(widths)) + 20
        box_h = (FONT_SIZE + line_gap) * len(lines) + 14
        draw.rectangle((x - 8, y - 8, x + box_w, y + box_h), fill=(0, 0, 0, 140))

    yy = y
    for s in lines:
        draw.text((x, yy), s, font=JP_FONT, fill=(255, 255, 255, 255))
        yy += FONT_SIZE + line_gap

    return pil_to_bgr(pil_img)


def load_region() -> dict:
    if not REGION_FILE.exists():
        raise FileNotFoundError(f"{REGION_FILE} not found. Run pick_roi.py first.")

    region = json.loads(REGION_FILE.read_text(encoding="utf-8"))
    for key in ("left", "top", "width", "height"):
        if key not in region:
            raise ValueError(f"region.json missing '{key}': {region}")
        region[key] = int(region[key])

    if region["width"] <= 0 or region["height"] <= 0:
        raise ValueError(f"invalid region size: {region}")

    return region


def _save_last_roi(bgr_roi: np.ndarray) -> None:
    try:
        cv2.imwrite(str(LAST_ROI_FILE), bgr_roi)
    except Exception:
        pass


def validate_roi_frame(frame: np.ndarray) -> tuple[bool, str]:
    if frame is None:
        return False, "input_none"
    if frame.size == 0:
        return False, "input_empty"

    h, w = frame.shape[:2]
    if h <= 1 or w <= 1:
        return False, "input_size_invalid"

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))
    std = float(np.std(gray))
    unique_ratio = float(len(np.unique(gray))) / 256.0

    if mean < 2.0:
        return False, "input_too_dark"
    if std < 1.0:
        return False, "input_monotone"
    if unique_ratio < 0.01:
        return False, "input_low_variation"

    return True, "ok"


_template_cache: list[dict] | None = None


def _normalize_for_template(bgr: np.ndarray, size: tuple[int, int] = (640, 360)) -> tuple[np.ndarray, np.ndarray]:
    resized = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 150)
    return gray, edges


def _get_template_cache() -> list[dict]:
    global _template_cache
    if _template_cache is not None:
        return _template_cache

    cache: list[dict] = []
    for file_name, label in TEMPLATE_FILE_TO_LABEL.items():
        path = TEMPLATES_DIR / file_name
        if not path.exists():
            continue
        bgr = cv2.imread(str(path))
        if bgr is None:
            continue
        gray, edges = _normalize_for_template(bgr)
        cache.append({"label": label, "gray": gray, "edges": edges, "path": str(path)})

    _template_cache = cache
    return cache


def template_classify_screen(bgr_roi: np.ndarray) -> dict:
    cache = _get_template_cache()
    if not cache:
        return {
            "label": UNKNOWN_LABEL,
            "confidence": 0.0,
            "reason": "template_unavailable",
            "raw": "",
            "latency_ms": 0,
            "finish_reason": "-",
            "cand": 0,
            "block_reason": "-",
            "err": "template_unavailable",
            "template_best": "-",
            "template_score": 0.0,
            "template_second": 0.0,
        }

    t0 = time.time()
    roi_gray, roi_edges = _normalize_for_template(bgr_roi)

    scored: list[tuple[float, str]] = []
    for t in cache:
        sad = float(np.mean(np.abs(roi_gray.astype(np.float32) - t["gray"].astype(np.float32)))) / 255.0
        esad = float(np.mean(np.abs(roi_edges.astype(np.float32) - t["edges"].astype(np.float32)))) / 255.0
        score = 1.0 - (0.58 * sad + 0.42 * esad)
        scored.append((score, t["label"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_label = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    confident = (best_score >= TEMPLATE_MIN_SCORE) and ((best_score - second_score) >= TEMPLATE_MIN_MARGIN)

    return {
        "label": best_label if confident else UNKNOWN_LABEL,
        "confidence": float(best_score if confident else 0.0),
        "reason": "template" if confident else "template_weak",
        "raw": "",
        "latency_ms": int((time.time() - t0) * 1000),
        "finish_reason": "-",
        "cand": 0,
        "block_reason": "-",
        "err": "" if confident else "template_weak",
        "template_best": best_label,
        "template_score": float(best_score),
        "template_second": float(second_score),
    }


# -------------------------
# Gemini
# -------------------------
_gemini_client: genai.Client | None = None


def gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        _gemini_client = genai.Client(api_key=api_key) if api_key else genai.Client()
    return _gemini_client


def _key_present() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def _append_gemini_log(line: str) -> None:
    if not DEBUG_GEMINI:
        return
    try:
        with GEMINI_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def bgr_to_jpeg_bytes(bgr: np.ndarray, quality: int, max_w: int) -> bytes:
    h, w = bgr.shape[:2]
    if w > max_w:
        scale = max_w / float(w)
        bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("jpeg encode failed")

    return buf.tobytes()


def _thinking_level_enum() -> types.ThinkingLevel:
    name = GEMINI_THINKING_LEVEL.strip().upper()
    mapping = {
        "MINIMAL": types.ThinkingLevel.MINIMAL,
        "LOW": types.ThinkingLevel.LOW,
        "MEDIUM": types.ThinkingLevel.MEDIUM,
        "HIGH": types.ThinkingLevel.HIGH,
    }
    return mapping.get(name, types.ThinkingLevel.MINIMAL)


def _extract_text_and_meta(resp) -> tuple[str, str, int, str]:
    text = ""
    finish_reason = "-"
    block_reason = "-"

    try:
        text = (getattr(resp, "text", None) or "").strip()
    except Exception:
        text = ""

    candidates = []
    try:
        candidates = list(getattr(resp, "candidates", None) or [])
    except Exception:
        candidates = []

    cand_count = len(candidates)

    if cand_count > 0:
        c0 = candidates[0]

        fr = getattr(c0, "finish_reason", None)
        if fr is not None:
            finish_reason = str(fr)

        if not text:
            try:
                parts = getattr(getattr(c0, "content", None), "parts", None) or []
                texts = [getattr(p, "text", "") for p in parts if getattr(p, "text", "")]
                text = "\n".join([x for x in texts if x]).strip()
            except Exception:
                pass

        # SDK/schema fallback
        if not text:
            try:
                c0_dict = c0.model_dump()
            except Exception:
                c0_dict = {}

            try:
                parts = (((c0_dict.get("content") or {}).get("parts")) or [])
                texts = [str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("text")]
                text = "\n".join(texts).strip()
            except Exception:
                pass

    try:
        pf = getattr(resp, "prompt_feedback", None)
        br = getattr(pf, "block_reason", None)
        if br is not None:
            block_reason = str(br)
    except Exception:
        pass

    return text, finish_reason, cand_count, block_reason


def _parse_label_digit_or_text(text: str) -> str:
    t = (text or "").strip().replace("\n", " ").strip().strip('"\'')

    for ch in t:
        if ch.isdigit():
            idx = int(ch)
            if 0 <= idx < len(CLASS_LABELS):
                return CLASS_LABELS[idx]

    t_lower = t.lower()
    for lab in CLASS_LABELS:
        if lab in t_lower:
            return lab

    aliases = {
        "login": "login_disconnect",
        "disconnect": "login_disconnect",
        "vm list": "vm_list",
        "storage": "storage_list",
        "network": "network_list",
        "vm create": "vm_create",
        "user": "user_add",
        "license": "license_view",
        "reboot": "host_reboot",
    }
    for k, v in aliases.items():
        if k in t_lower:
            return v

    return UNKNOWN_LABEL


def _parse_structured_label(raw_text: str, parsed_obj) -> tuple[str, float]:
    label = UNKNOWN_LABEL
    confidence = 0.0

    obj = parsed_obj if isinstance(parsed_obj, dict) else None
    if obj is None and raw_text:
        try:
            maybe = json.loads(raw_text)
            if isinstance(maybe, dict):
                obj = maybe
        except Exception:
            obj = None

    if obj is not None:
        label_raw = str(obj.get("label", "")).strip()
        if label_raw in CLASS_LABELS:
            label = label_raw
        is_esxi = obj.get("is_esxi", True)
        is_esxi_false = False
        if isinstance(is_esxi, bool):
            is_esxi_false = (is_esxi is False)
        elif isinstance(is_esxi, (int, float)):
            is_esxi_false = (float(is_esxi) == 0.0)
        elif isinstance(is_esxi, str):
            is_esxi_false = is_esxi.strip().lower() in {"false", "0", "no", "n"}
        if is_esxi_false:
            label = UNKNOWN_LABEL
        try:
            conf_val = float(obj.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, conf_val))
        except Exception:
            confidence = 0.0
    else:
        s = (raw_text or "").strip()
        # Structured path should not trust partial JSON text.
        if s.startswith("{") or s.startswith("["):
            return UNKNOWN_LABEL, 0.0
        label = _parse_label_digit_or_text(raw_text)
        confidence = 0.92 if label != UNKNOWN_LABEL else 0.0

    return label, confidence


def gemini_classify_screen(bgr_roi: np.ndarray) -> dict:
    """
    Returns:
      {"label","confidence","reason","raw","latency_ms","finish_reason","cand","block_reason","err"}
    """
    t0 = time.time()

    ok, input_reason = validate_roi_frame(bgr_roi)
    if not ok:
        return {
            "label": UNKNOWN_LABEL,
            "confidence": 0.0,
            "reason": input_reason,
            "raw": "",
            "latency_ms": int((time.time() - t0) * 1000),
            "finish_reason": "-",
            "cand": 0,
            "block_reason": "-",
            "err": input_reason,
        }

    img_bytes = bgr_to_jpeg_bytes(bgr_roi, quality=GEMINI_JPEG_QUALITY, max_w=GEMINI_MAX_W)
    client = gemini_client()

    structured_schema = {
        "type": "OBJECT",
        "properties": {
            "label": {"type": "STRING", "enum": CLASS_LABELS},
            "confidence": {"type": "NUMBER"},
            "is_esxi": {"type": "BOOLEAN"},
        },
        "required": ["label", "is_esxi"],
    }

    prompt_json = (
        "You are classifying VMware ESXi Host Client screenshots.\n"
        "Return compact JSON only with keys: label, is_esxi, confidence.\n"
        "Do not include any prose, explanations, or extra keys.\n"
        "Classify by the foreground modal/dialog when visible.\n"
        "Allowed labels: " + ", ".join(CLASS_LABELS)
    )
    prompt_text = (
        "Classify this VMware ESXi Host Client screenshot.\n"
        "Return exactly one label token from the list, no extra text.\n"
        "Classify by the foreground modal/dialog when visible.\n"
        "Allowed labels: " + ", ".join(CLASS_LABELS)
    )

    def _call(prompt: str, tag: str, structured: bool) -> dict:
        cfg_kwargs = {
            "temperature": 0.0,
            "max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json" if structured else "text/plain",
            "thinking_config": types.ThinkingConfig(thinking_level=_thinking_level_enum()),
        }
        if structured:
            cfg_kwargs["response_schema"] = structured_schema
        cfg = types.GenerateContentConfig(**cfg_kwargs)

        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")],
            config=cfg,
        )

        raw, finish_reason, cand_count, block_reason = _extract_text_and_meta(resp)
        parsed_obj = getattr(resp, "parsed", None)
        if structured:
            label, conf = _parse_structured_label(raw, parsed_obj)
        else:
            label = _parse_label_digit_or_text(raw)
            conf = 0.92 if label != UNKNOWN_LABEL else 0.0

        latency_ms = int((time.time() - t0) * 1000)

        err_parts: list[str] = []
        if not raw and not isinstance(parsed_obj, dict):
            err_parts.append("empty_text")
        if cand_count == 0:
            err_parts.append("no_candidates")
        if label != UNKNOWN_LABEL and conf < GEMINI_MIN_CONF:
            err_parts.append(f"low_conf:{conf:.2f}")
            label = UNKNOWN_LABEL
            conf = 0.0
        if label == UNKNOWN_LABEL:
            err_parts.append("parse_failed")
        if block_reason not in ("-", "", "None", "BLOCK_REASON_UNSPECIFIED"):
            err_parts.append(f"blocked:{block_reason}")

        err = "|".join(err_parts)

        _append_gemini_log(
            f"{now_ts()} {tag} raw='{raw[:120]}' label={label} conf={conf:.2f} finish={finish_reason} cand={cand_count} block={block_reason} err={err}"
        )

        return {
            "label": label,
            "confidence": float(conf),
            "reason": tag,
            "raw": (raw or "").strip()[:200],
            "latency_ms": latency_ms,
            "finish_reason": finish_reason,
            "cand": cand_count,
            "block_reason": block_reason,
            "err": err,
            "template_best": "-",
            "template_score": 0.0,
            "template_second": 0.0,
        }

    try:
        r1 = _call(prompt_json, "gemini(json)", structured=True)
        if r1["label"] != UNKNOWN_LABEL:
            return r1

        r2 = _call(prompt_text, "gemini(text)", structured=False)
        if r2["label"] != UNKNOWN_LABEL:
            return r2

        unresolved = {
            "label": UNKNOWN_LABEL,
            "confidence": 0.0,
            "reason": "gemini_unresolved",
            "raw": (r2.get("raw") or r1.get("raw") or "")[:200],
            "latency_ms": int((time.time() - t0) * 1000),
            "finish_reason": r2.get("finish_reason", "-"),
            "cand": r2.get("cand", 0),
            "block_reason": r2.get("block_reason", "-"),
            "err": "|".join(filter(None, [r1.get("err", ""), r2.get("err", "")])),
            "template_best": "-",
            "template_score": 0.0,
            "template_second": 0.0,
        }
        if ENABLE_TEMPLATE_FALLBACK:
            tf = template_classify_screen(bgr_roi)
            if tf["label"] != UNKNOWN_LABEL:
                tf["reason"] = f"template_fallback_after_{r2.get('reason', 'gemini')}"
                tf["err"] = unresolved["err"] or tf.get("err", "")
                return tf
            unresolved["template_best"] = tf.get("template_best", "-")
            unresolved["template_score"] = tf.get("template_score", 0.0)
            unresolved["template_second"] = tf.get("template_second", 0.0)
        return unresolved
    except Exception as e:
        failed = {
            "label": UNKNOWN_LABEL,
            "confidence": 0.0,
            "reason": f"gemini_exception:{type(e).__name__}",
            "raw": "",
            "latency_ms": int((time.time() - t0) * 1000),
            "finish_reason": "-",
            "cand": 0,
            "block_reason": "-",
            "err": str(e)[:180],
            "template_best": "-",
            "template_score": 0.0,
            "template_second": 0.0,
        }
        if ENABLE_TEMPLATE_FALLBACK:
            tf = template_classify_screen(bgr_roi)
            if tf["label"] != UNKNOWN_LABEL:
                tf["reason"] = "template_fallback_after_gemini_exception"
                tf["err"] = failed["err"]
                return tf
            failed["template_best"] = tf.get("template_best", "-")
            failed["template_score"] = tf.get("template_score", 0.0)
            failed["template_second"] = tf.get("template_second", 0.0)
        return failed


def summarize_with_gemini(event_log: dict) -> str:
    client = gemini_client()
    prompt = (
        "Create a Markdown summary for this ESXi Host Client demo session.\n"
        "Use only facts contained in the input JSON. Do not add speculation.\n"
        "Include timeline, state transitions, durations, and manual operations if any.\n"
        "Input JSON:\n" + json.dumps(event_log, ensure_ascii=False)
    )

    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=900),
    )
    return (getattr(resp, "text", None) or "").strip()


def _format_sec(sec: float) -> str:
    return f"{float(sec):.1f}s"


def _parse_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def _label_name(label: str) -> str:
    return OVERLAY_MAP.get(label, label)


def build_local_summary_markdown(event_log: dict) -> str:
    start_ts = str(event_log.get("start_ts", "-"))
    end_ts = str(event_log.get("end_ts", "-"))
    elapsed = float(event_log.get("elapsed_sec", 0.0) or 0.0)
    region = event_log.get("region", {}) or {}
    state_events = list(event_log.get("state_events", []) or [])
    ops_events = list(event_log.get("ops_events", []) or [])
    durations = dict(event_log.get("durations", {}) or {})
    last_g = dict(event_log.get("last_gemini", {}) or {})

    lines: list[str] = []
    lines.append("# ESXi CV セッションサマリ")
    lines.append("")
    lines.append("## セッション情報")
    lines.append(f"- 開始: {start_ts}")
    lines.append(f"- 終了: {end_ts}")
    lines.append(f"- 経過時間: {_format_sec(elapsed)}")
    lines.append(
        f"- ROI: left={region.get('left', '-')}, top={region.get('top', '-')}, "
        f"w={region.get('width', '-')}, h={region.get('height', '-')}"
    )
    lines.append("")

    lines.append("## 状態遷移タイムライン")
    lines.append("| 時刻 | +秒 | 状態 | 判定理由 |")
    lines.append("|---|---:|---|---|")
    lines.append(f"| {start_ts} | +0.0 | unknown | 初期状態 |")

    start_epoch = _parse_ts(start_ts)
    compressed_seq: list[str] = ["unknown"]
    for ev in state_events:
        ts = str(ev.get("ts", "-"))
        label = str(ev.get("label", UNKNOWN_LABEL))
        reason = str(ev.get("reason", "-"))
        ev_epoch = _parse_ts(ts)
        plus = 0.0
        if start_epoch is not None and ev_epoch is not None:
            plus = max(0.0, ev_epoch - start_epoch)
        lines.append(f"| {ts} | +{plus:.1f} | {label} | {reason} |")
        if not compressed_seq or compressed_seq[-1] != label:
            compressed_seq.append(label)
    lines.append("")

    lines.append("## 状態ごとの滞在時間")
    if durations:
        for k, v in sorted(durations.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {k} ({_label_name(k)}): {_format_sec(float(v))}")
    else:
        lines.append("- 記録なし")
    lines.append("")

    lines.append("## 状態シーケンス（圧縮）")
    lines.append("- " + " -> ".join(compressed_seq))
    lines.append("")

    lines.append("## 手動操作ログ")
    if ops_events:
        lines.append("| 時刻 | 操作 | 状態 |")
        lines.append("|---|---|---|")
        for op in ops_events:
            lines.append(
                f"| {op.get('ts', '-')} | {op.get('op', '-')} | {op.get('state', '-')} |"
            )
    else:
        lines.append("- 手動操作は記録されていません。")
    lines.append("")

    lines.append("## 最終推論")
    lines.append(f"- label: {last_g.get('label', '-')}")
    lines.append(f"- reason: {last_g.get('reason', '-')}")
    lines.append(f"- confidence: {last_g.get('confidence', '-')}")
    lines.append(f"- latency_ms: {last_g.get('latency_ms', '-')}")
    lines.append(f"- finish_reason: {last_g.get('finish_reason', '-')}")
    lines.append(f"- cand/block: {last_g.get('cand', '-')} / {last_g.get('block_reason', '-')}")
    lines.append(f"- err: {last_g.get('err', '-') or '-'}")
    lines.append("")

    return "\n".join(lines).strip() + "\n"


# -------------------------
# Main loop
# -------------------------

def _most_common_label(labels: deque[str]) -> str:
    if not labels:
        return UNKNOWN_LABEL
    return Counter(labels).most_common(1)[0][0]


def _right_streak(labels: deque[str], label: str) -> int:
    n = 0
    for x in reversed(labels):
        if x == label:
            n += 1
        else:
            break
    return n


def _safe_grab_roi(sct: mss.mss, region: dict) -> tuple[np.ndarray | None, str]:
    try:
        shot = np.array(sct.grab(region))
        frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        return frame, "ok"
    except Exception as e:
        return None, f"grab_error:{type(e).__name__}"


def main() -> None:
    global SHOW_ROI_WINDOW

    region = load_region()
    template_count = len(_get_template_cache()) if ENABLE_TEMPLATE_FALLBACK else 0

    print(f"[INFO] region: {region}", flush=True)
    print(f"[INFO] model : {GEMINI_MODEL}", flush=True)
    print(f"[INFO] key   : {'Y' if _key_present() else 'N'}", flush=True)
    print(
        f"[INFO] template fallback: {'ON' if ENABLE_TEMPLATE_FALLBACK else 'OFF'} "
        f"(loaded={template_count}, min_score={TEMPLATE_MIN_SCORE:.2f}, min_margin={TEMPLATE_MIN_MARGIN:.2f})",
        flush=True,
    )
    print(f"[INFO] gemini min_conf: {GEMINI_MIN_CONF:.2f}", flush=True)
    print(
        f"[INFO] fast switch: streak>={FAST_SWITCH_STREAK} conf>={FAST_SWITCH_MIN_CONF:.2f} "
        f"modal={sorted(MODAL_PRIORITY_LABELS)}",
        flush=True,
    )
    print(f"[INFO] summary mode: {'local+gemini' if USE_GEMINI_SUMMARY else 'local'}", flush=True)
    print("[INFO] press q to quit", flush=True)

    start_time = time.time()
    start_ts = now_ts()

    state_events = deque(maxlen=5000)
    ops_events = deque(maxlen=5000)
    state_durations: Counter[str] = Counter()

    stable_label = UNKNOWN_LABEL
    stable_conf = 0.0
    last_state_change_t = time.time()
    hold_until = 0.0
    vote_labels: deque[str] = deque(maxlen=VOTE_WINDOW)

    recent_ops = deque(maxlen=8)

    executor = ThreadPoolExecutor(max_workers=1)
    pending = None
    next_submit = 0.0
    gemini_calls = 0
    last_input_status = "ok"

    last_g = {
        "label": UNKNOWN_LABEL,
        "confidence": 0.0,
        "reason": "init",
        "raw": "",
        "latency_ms": 0,
        "finish_reason": "-",
        "cand": 0,
        "block_reason": "-",
        "err": "",
        "template_best": "-",
        "template_score": 0.0,
        "template_second": 0.0,
        "ts": None,
    }

    fps = 0.0
    last_frame_t = time.time()

    with mss.mss() as sct:
        cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)

        while True:
            frame, grab_reason = _safe_grab_roi(sct, region)
            now = time.time()

            dt = now - last_frame_t
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else (1.0 / dt)
            last_frame_t = now

            if frame is None:
                last_input_status = grab_reason
                frame = np.zeros((max(120, region["height"]), max(200, region["width"]), 3), dtype=np.uint8)
            else:
                ok, reason = validate_roi_frame(frame)
                last_input_status = "ok" if ok else reason

            if pending is not None and pending.done():
                try:
                    res = pending.result()
                except Exception as e:
                    res = {
                        "label": UNKNOWN_LABEL,
                        "confidence": 0.0,
                        "reason": f"future_exception:{type(e).__name__}",
                        "raw": "",
                        "latency_ms": 0,
                        "finish_reason": "-",
                        "cand": 0,
                        "block_reason": "-",
                        "err": str(e)[:180],
                    }

                last_g.update(res)
                last_g["ts"] = now_ts()
                pending = None

                if last_g["label"] == UNKNOWN_LABEL:
                    if now >= hold_until and stable_label != UNKNOWN_LABEL:
                        state_durations[stable_label] += max(0.0, now - last_state_change_t)
                        stable_label = UNKNOWN_LABEL
                        stable_conf = 0.0
                        last_state_change_t = now
                        hold_until = now + min(HOLD_SEC, 0.3)
                        state_events.append({"ts": now_ts(), "label": stable_label, "reason": last_g.get("reason", "")})
                else:
                    vote_labels.append(last_g["label"])
                    voted = _most_common_label(vote_labels)
                    curr_label = last_g["label"]
                    curr_conf = float(last_g.get("confidence", 0.0) or 0.0)
                    curr_streak = _right_streak(vote_labels, curr_label)

                    fast_switch = (
                        curr_label != stable_label
                        and curr_conf >= FAST_SWITCH_MIN_CONF
                        and curr_streak >= FAST_SWITCH_STREAK
                    )
                    modal_switch = (
                        curr_label != stable_label
                        and curr_label in MODAL_PRIORITY_LABELS
                        and curr_conf >= max(0.70, FAST_SWITCH_MIN_CONF - 0.10)
                    )

                    if (now >= hold_until and voted != stable_label) or fast_switch or modal_switch:
                        state_durations[stable_label] += max(0.0, now - last_state_change_t)
                        stable_label = curr_label if (fast_switch or modal_switch) else voted
                        stable_conf = curr_conf
                        last_state_change_t = now
                        hold_until = now + HOLD_SEC
                        state_events.append({"ts": now_ts(), "label": stable_label, "reason": last_g.get("reason", "")})

            if pending is None and now >= next_submit:
                roi_for_gem = frame.copy()
                if SAVE_LAST_ROI:
                    _save_last_roi(roi_for_gem)
                pending = executor.submit(gemini_classify_screen, roi_for_gem)
                gemini_calls += 1
                next_submit = now + GEMINI_INTERVAL_SEC

            if SHOW_ROI_WINDOW:
                preview = frame
                maxw = 720
                h, w = preview.shape[:2]
                if w > maxw:
                    s = maxw / float(w)
                    preview = cv2.resize(preview, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
                cv2.imshow(ROI_WINDOW_TITLE, preview)

            overlay_text = OVERLAY_MAP.get(stable_label, stable_label)
            next_in = max(0.0, next_submit - now)
            raw_show = (last_g.get("raw", "") or "").strip() or "-"
            err_show = (last_g.get("err", "") or "-").strip()
            last_op = recent_ops[0] if recent_ops else "-"

            lines = [
                f"state(stable): {stable_label} conf={stable_conf:.2f}  {time.strftime('%H:%M:%S')}",
                f"summary: {overlay_text}",
                f"input: {last_input_status}",
                f"gemini: {'pending' if pending is not None else 'idle'} model={GEMINI_MODEL} key={'Y' if _key_present() else 'N'}",
                f"gemini(last): {last_g.get('latency_ms', 0)}ms reason={last_g.get('reason', '-')} finish={last_g.get('finish_reason', '-')}",
                f"gemini(meta): cand={last_g.get('cand', 0)} block={last_g.get('block_reason', '-')} raw={raw_show}",
                f"template(meta): best={last_g.get('template_best', '-')} score={float(last_g.get('template_score', 0.0)):.3f} second={float(last_g.get('template_second', 0.0)):.3f}",
                f"gemini(err): {err_show}",
                f"next={next_in:.1f}s calls={gemini_calls} interval={GEMINI_INTERVAL_SEC:.1f}s fps={fps:.1f}",
                f"roi: left={region['left']} top={region['top']} w={region['width']} h={region['height']} (v:ROI)",
                f"manual(last): {last_op}",
                "manual keys: 1=create 2=delete 3=on 4=off 5=reset 6=snap+ 7=snap- 8=iso+ 9=iso-",
            ]

            frame2 = draw_lines(frame, lines)
            cv2.imshow(WINDOW_TITLE, frame2)

            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break

            if k == ord("v"):
                SHOW_ROI_WINDOW = not SHOW_ROI_WINDOW
                if not SHOW_ROI_WINDOW:
                    try:
                        cv2.destroyWindow(ROI_WINDOW_TITLE)
                    except Exception:
                        pass
                continue

            if k in OP_KEY_MAP:
                op = OP_KEY_MAP[k]
                op_event = {"ts": now_ts(), "op": op, "state": stable_label}
                ops_events.append(op_event)
                recent_ops.appendleft(f"{op_event['ts']} {op} @ {stable_label}")

    executor.shutdown(wait=False)
    cv2.destroyAllWindows()

    end_time = time.time()
    end_ts = now_ts()
    state_durations[stable_label] += max(0.0, end_time - last_state_change_t)

    event_log = {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "elapsed_sec": round(end_time - start_time, 3),
        "region": region,
        "state_events": list(state_events),
        "ops_events": list(ops_events),
        "durations": dict(state_durations),
        "last_gemini": last_g,
    }

    run_id = int(start_time)
    run_json_path = OUT_DIR / f"run_{run_id}.json"
    run_json_path.write_text(json.dumps(event_log, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = OUT_DIR / f"summary_{run_id}.md"
    summary_text = build_local_summary_markdown(event_log)
    if USE_GEMINI_SUMMARY:
        try:
            ai_text = summarize_with_gemini(event_log).strip()
            if ai_text:
                summary_text += "\n## Gemini追記（任意）\n\n" + ai_text + "\n"
        except Exception as e:
            summary_text += f"\n## Gemini追記（任意）\n\n- スキップ: {type(e).__name__}: {e}\n"

    summary_path.write_text(summary_text, encoding="utf-8")

    print(f"[INFO] log saved: {run_json_path}", flush=True)
    print(f"[INFO] summary saved: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
