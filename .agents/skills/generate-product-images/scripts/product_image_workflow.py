from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from image2_combo_batch import image_output_format, request_image
from image2_test import ENV_PATH, ROOT, load_mixed_env, parse_bool, read_prompt_objects, reference_images


SOURCE_ROOT = ROOT / "原始商品图"
ACCESSORY_ROOT = ROOT / "配件超市"
OUTPUT_ROOT = ROOT / "组合"
SHOT_FOLDERS = {
    "main": "主图",
    "size": "尺寸图",
    "lifestyle-scene": "场景图",
    "detail": "细节图",
    "comparison": "对比图",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DOC_SUFFIXES = {".txt", ".md", ".json", ".docx", ".pdf"}
FACT_CACHE_VERSION = 1
MUSEFORGE_EVENT_PREFIX = "MUSEFORGE_EVENT "
MUSEFORGE_RUN_SPEC_SCHEMA = "museforge.run-spec"
MUSEFORGE_RUN_SPEC_VERSIONS = {1, 2}
MUSEFORGE_RUN_SPEC_MAX_BYTES = 256 * 1024


@dataclass(frozen=True)
class MuseForgeRunConfig:
    run_id: str | None
    run_dir: Path | None
    variants: int

    @property
    def staged(self) -> bool:
        return self.run_dir is not None


@dataclass(frozen=True)
class GenerationJob:
    product: str
    task: str
    shot: str
    candidate_index: int
    job_dir: Path
    target: Path
    refs: list[Path]
    prompt: dict[str, Any]
    prompt_index: int
    prompt_total: int
    run_dir: Path | None
    allow_overwrite: bool

COMPLIANCE = (
    "Bright daytime environment only. Show the complete product. No night, dusk, low light, children, "
    "people who appear underage, wall plugs, certification marks, voltage claims, wireless symbols, "
    "Bluetooth symbols, environmental claims, waterproof claims, brand logos, copyrighted characters, "
    "unrelated props, distorted geometry, invented functions, unreadable decorative text, Chinese text, "
    "or any visible non-English wording. Every visible word must use natural US English."
)

US_ENGLISH_TEXT_POLICY = (
    "US marketplace language policy: every visible headline, caption, dimension label, feature callout, "
    "comparison heading, icon label, and supporting word must be written in concise, natural US English. "
    "Never render Chinese source wording, bilingual text, pinyin, placeholder copy, garbled characters, or "
    "machine-like literal translations. Chinese product information is internal evidence only. Convert only "
    "verified facts into short idiomatic English labels; omit any fact that cannot be translated confidently."
)

INTERACTION_POLICIES = (
    (
        "wig-display-form",
        ("假发", "synthetic wig", "lace-front wig", "lace front wig"),
        {
            "interaction_mode": "complete wig fitted on a neutral faceless display head",
            "support_surface": "a stable neutral faceless mannequin head and stand sized to support the complete wig without showing a person",
            "contact_points": "the inner cap sits naturally around the display-head crown while the center part, front lace edge, curls, and full 24-inch length remain visible",
            "load_direction": "the display head and stand carry the wig weight vertically with believable drape, curl volume, and gravity",
            "completed_state": "show the complete wig correctly fitted and centered on the neutral display head, with the hairline, middle part, front and back length, and curl pattern readable",
            "forbidden_state": "no real person, face, skin-detail portrait, floating wig, hanging by hair, exposed stand through the cap, distorted hairline, missing back volume, invented color blend, or unsupported human-hair claim",
        },
    ),
    (
        "vehicle-camera-mounted",
        ("行车记录仪", "dash cam", "dashcam", "driving recorder"),
        {
            "interaction_mode": "verified in-vehicle camera mounting",
            "support_surface": "a clean windshield or dashboard mounting zone compatible with the bracket or suction structure visible in the references",
            "contact_points": "the complete verified mount sits flush against its real support while the camera body is fully seated and aimed through the windshield",
            "load_direction": "the mount carries the camera weight without sagging, floating, windshield penetration, or an implausible center of mass",
            "completed_state": "show the complete camera system securely installed in a credible driver-safe position with every visible secondary camera oriented consistently",
            "forbidden_state": "no floating camera, fake windshield contact, hidden mount, dashboard penetration, blocked driver sightline, invented holder, loose unsupported module, or impossible cable path",
        },
    ),
    (
        "table-lamp-standing",
        ("桌面台灯", "table lamp", "desk lamp"),
        {
            "interaction_mode": "stable freestanding table-lamp use",
            "support_surface": "a level, dry, stable tabletop or shelf broad enough for the complete verified base",
            "contact_points": "the entire underside of the product base rests naturally on the surface with no gap or hidden support",
            "load_direction": "gravity passes vertically through the lamp body into the centered base with a credible, upright center of mass",
            "completed_state": "show the complete lamp standing upright on its own verified base, with its inline cable and switch arranged naturally when visible",
            "forbidden_state": "no floating base, wall mounting, hanging, invented stand, unstable tilt, cable used as support, wall plug, power adapter, or impossible cable path",
        },
    ),
    (
        "clamp-mounted",
        ("夹", "clamp", "clip on", "clip-on"),
        {
            "interaction_mode": "clamp-mounted use",
            "support_surface": "a sturdy edge, horizontal rail, board, or bar whose shape and thickness fit the verified clamp",
            "contact_points": "the clamp jaws visibly wrap around the support, with both opposing gripping faces in firm contact",
            "load_direction": "the clamp resists gravity and keeps the product center of mass within a credible supported position",
            "completed_state": "show the clamp fully engaged and closed around the support while the complete product extends naturally from it",
            "forbidden_state": "never stand the clamp on a flat surface as if it were a base; no floating jaws, fake contact, penetration, unstable balance, or hidden attachment",
        },
    ),
    (
        "adhesive-mounted",
        ("自粘", "背胶", "adhesive", "peel and stick", "self-stick"),
        {
            "interaction_mode": "adhesive-mounted use",
            "support_surface": "a verified compatible clean, smooth, dry surface",
            "contact_points": "the full adhesive back sits flush against the surface with aligned edges",
            "load_direction": "the bonded area visibly supports the product without gaps or implausible leverage",
            "completed_state": "show a fully aligned, pressed, flush installation with the complete product readable",
            "forbidden_state": "no floating, bubbles, lifted corners, wall penetration, nails, screws, glue guns, or invented mounting hardware",
        },
    ),
    (
        "hooked-or-hanging",
        ("挂钩", "悬挂", "吊挂", "hook", "hanging"),
        {
            "interaction_mode": "hooked or hanging use",
            "support_surface": "a visible verified load-bearing rail, edge, hole, or rod",
            "contact_points": "the hook, ring, or loop passes around or through the real support and is visibly closed or seated",
            "load_direction": "gravity pulls downward through the hook or loop into the support",
            "completed_state": "show the complete item hanging naturally from a visible load point",
            "forbidden_state": "no floating, background-only contact, open disconnected rings, impossible load paths, or hidden supports",
        },
    ),
    (
        "inserted-or-connected",
        ("插入", "卡扣", "接口", "连接", "insert", "slot", "connector", "buckle"),
        {
            "interaction_mode": "inserted, latched, or connected use",
            "support_surface": "the verified matching slot, interface, or receiving structure",
            "contact_points": "matching parts align on the same axis and engage to a credible depth",
            "load_direction": "the connection carries force along the intended interface without bending or distortion",
            "completed_state": "show the verified parts correctly aligned and fully seated or latched",
            "forbidden_state": "no mismatched interfaces, invented adapters, partial penetration, deformation, floating connectors, or unverified third-party compatibility",
        },
    ),
)

DEFAULT_INTERACTION_POLICY = {
    "interaction_mode": "verified use-state or stable product presentation",
    "support_surface": "a surface or support explicitly compatible with the supplied facts and visible product structure",
    "contact_points": "show every required base, support, attachment, or contact point clearly and completely",
    "load_direction": "keep gravity, balance, support, and product orientation physically credible",
    "completed_state": "show a complete, immediately understandable, real-world usable state; if the mechanism is unverified, use a neutral stable presentation",
    "forbidden_state": "no floating, penetration, fake contact, unstable balance, hidden support, invented attachment, or treating a clamp, hook, cable, or decorative part as a base",
}

SENSITIVE_SOURCE_PATTERNS = (
    r"power\s+adapter",
    r"high\s+voltage",
    r"\badapter\b",
    r"\b(?:kid|kids|child|children|boy|girl)\b",
    r"\b(?:wireless|bluetooth|fcc|phone\s+control)\b",
    r"\b(?:eco-friendly|environmental\s+friendly|environment\s+protection)\b",
    r"\b(?:outdoor|water\s*proof|waterproof)\b",
    r"儿童(?:房|乐园|使用)?",
)

PROMPT_FACT_BLOCKLIST = (
    r"\b(?:wireless|bluetooth|fcc|phone\s+control)\b",
    r"\b(?:power\s+adapter|adapter|high\s+voltage)\b",
    r"\b(?:outdoor|water\s*proof|waterproof)\b",
    r"\b(?:eco-friendly|environmental\s+friendly|environment\s+protection)\b",
    r"\b\d+(?:\.\d+)?\s*v\b",
    r"(?:无线|蓝牙|感应|防水|雷雨|夜晚|夜间|户外|环保|认证|证书|资质报告)",
)

REFERENCE_POLICY = (
    "Treat every reference image as a product-identity constraint, never as a composition template. "
    "Preserve exact product shape, count, color, material, construction, visible parts, and relative scale. "
    "Do not copy the source background, crop, camera angle, tabletop, wall, lighting, props, or layout. "
    "Create an original composition appropriate to the requested image type and never invent parts or functions."
)

SCENE_DOMAIN_KEYWORDS = {
    "home-living": ("home decor", "wall decor", "living room", "bedroom", "mirror", "curtain", "家居", "墙贴", "装饰", "客厅", "卧室", "镜面"),
    "kitchen-dining": ("kitchen", "dining", "cook", "tableware", "utensil", "厨房", "餐厨", "烹饪", "餐具", "厨具"),
    "bath-personal-care": ("bathroom", "bath", "toiletry", "personal care", "grooming", "洗漱", "浴室", "卫浴", "护理", "美容"),
    "office-study": ("office", "study", "desktop", "stationery", "document", "办公", "学习", "桌面", "文具", "文件"),
    "workshop-maintenance": ("tool", "repair", "maintenance", "workshop", "hardware", "safety glove", "工具", "维修", "维护", "五金", "劳保"),
    "vehicle-travel": ("vehicle", "automotive", "dashboard", "dash cam", "driving recorder", "trunk", "luggage", "travel", "汽车", "车载", "行车记录仪", "后备箱", "行李", "旅行", "出行"),
    "garden-daylight": ("garden", "gardening", "plant", "pruning", "patio", "园艺", "花园", "植物", "修剪", "庭院"),
    "fitness-recreation": ("fitness", "exercise", "sport", "training", "recreation", "健身", "运动", "训练", "休闲"),
    "pet-care": ("pet", "cat", "dog", "feeding", "grooming", "宠物", "猫", "犬", "喂养"),
    "retail-gifting": ("gift", "souvenir", "party favor", "赠品", "礼品", "礼赠", "纪念品"),
}

SCENE_DOMAINS = {
    "home-living": (
        ("bright contemporary living area", "calm everyday use and a subtle home upgrade"),
        ("sunlit bedroom, dressing, or entry zone", "an orderly daily routine without showing a person"),
    ),
    "kitchen-dining": (
        ("bright clean kitchen preparation area", "organized food or table preparation supported by verified use"),
        ("sunlit dining-side organization area", "an easy serving, storage, or cleanup routine supported by verified facts"),
    ),
    "bath-personal-care": (
        ("bright dry washroom organization area", "a clean personal-care routine without water-performance implications"),
        ("fresh daylight vanity or grooming corner", "an easy preparation ritual without a visible person"),
    ),
    "office-study": (
        ("efficient daylight office or study area", "organized preparation and focused practical use"),
        ("bright document and desktop organization zone", "a clear work routine with restrained supporting objects"),
    ),
    "workshop-maintenance": (
        ("bright dry workshop bench", "credible setup, repair, or maintenance supported by verified facts"),
        ("organized tool and equipment preparation zone", "safe practical readiness without invented performance claims"),
    ),
    "vehicle-travel": (
        ("bright dry vehicle-side preparation area", "credible vehicle organization or maintenance supported by verified use"),
        ("daylit luggage or cargo organization zone", "an orderly travel preparation routine without performance claims"),
    ),
    "garden-daylight": (
        ("bright dry garden work area in clear daytime", "plant care or garden preparation supported by verified use"),
        ("sunlit sheltered patio work zone", "organized garden-side setup with no weather-resistance implication"),
    ),
    "fitness-recreation": (
        ("bright training preparation area", "organized exercise or recreation readiness supported by verified use"),
        ("clear daytime sports-side equipment zone", "an attainable activity setup without showing a person"),
    ),
    "pet-care": (
        ("bright clean pet-living organization area", "a calm feeding, grooming, or care routine supported by verified facts"),
        ("sunlit pet-supply preparation zone", "orderly daily care without showing an animal using an unverified function"),
    ),
    "retail-gifting": (
        ("restrained daylight gifting presentation area", "a coordinated bundle when direct functional use would be misleading"),
        ("bright neutral retail display surface", "clear bundle contents with a non-promotional presentation"),
    ),
    "neutral-commercial": (
        ("bright neutral commercial product setting", "a factual product-readable presentation when use context is uncertain"),
        ("high-key modular display surface", "a clean category-neutral arrangement with no invented use context"),
    ),
}

VISUAL_VARIANTS = (
    ("eye-level three-quarter view with gentle foreground depth", "environmental thirds composition with the complete product as the visual anchor", "soft left-side daylight with clean commercial fill", "quiet, refined, attainable"),
    ("near-first-person standing viewpoint", "foreground-guided composition leading naturally to the complete product", "bright right-side daylight with soft neutral bounce", "warm, familiar, comfortably lived-in"),
    ("slight elevated front-side view", "clean diagonal arrangement with generous negative space", "broad top-side diffused daylight", "clear, capable, low-noise"),
    ("straight-on spatial mid-shot", "symmetrical framing around the complete product", "even frontal daylight with restrained soft shadows", "welcoming, practical, composed"),
    ("45-degree side-front product-readable view", "layered depth with quiet supporting objects and no clutter", "soft overhead daylight plus subtle edge separation", "useful, trustworthy, satisfying"),
    ("slight elevated catalog view", "balanced asymmetrical grouping with clear product hierarchy", "bright uniform studio daylight with natural contact shadows", "considered, polished, non-promotional"),
    ("close environmental mid-shot while retaining the full product", "off-center hero placement with a calm side information zone", "high-key side daylight with controlled highlights", "clean, tactile, quietly aspirational"),
    ("moderate top-front perspective", "structured top-to-bottom visual flow with complete items unobstructed", "large diffused daylight source with soft directional definition", "hands-on, approachable, credible"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare product-image prompts and optionally run the separate Image2 generation stage.")
    parser.add_argument("action", choices=("prepare", "preview", "generate", "all"))
    parser.add_argument("--product", help="One folder name under 原始商品图. Omit to process all products.")
    parser.add_argument("--task", action="append", help="One task folder under 组合/<product>, such as 单品 or an accessory name. Repeat to select multiple tasks.")
    parser.add_argument("--shot", action="append", choices=tuple(SHOT_FOLDERS), help="Generate only one image type such as lifestyle-scene. Repeat to select multiple types.")
    parser.add_argument("--combinations-only", action="store_true", help="Process accessory combinations only and skip 单品 for every selected product.")
    parser.add_argument("--variants-only", action="store_true", help="Process only prebuilt 单品-* variant tasks, skipping regular 单品 and all accessory combinations.")
    parser.add_argument("--concurrency", type=int, default=None, help="Global concurrent Image2 requests, maximum 10.")
    parser.add_argument("--refresh-prompts", action="store_true", help="Rebuild prompts.json and prompts.md without changing existing images.")
    parser.add_argument("--overwrite", action="store_true", help="Regenerate images that already exist.")
    return parser.parse_args()


def read_document(path: Path) -> str:
    try:
        if path.suffix.lower() in {".txt", ".md", ".json"}:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        if path.suffix.lower() == ".docx":
            from docx import Document

            return "\n".join(p.text for p in Document(path).paragraphs if p.text.strip())
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)
    except Exception as exc:
        print(f"warning: could not read document {path}: {exc}")
    return ""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fact_source_fingerprint(folder: Path) -> list[dict[str, Any]]:
    fingerprint: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
            continue
        stat = path.stat()
        fingerprint.append({
            "path": path.relative_to(ROOT).as_posix(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": file_sha256(path),
        })
    return fingerprint


def fact_cache_path(folder: Path) -> Path:
    identity = folder.relative_to(ROOT).as_posix().encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()[:20]
    return OUTPUT_ROOT / "_workflow-cache" / "facts" / f"{key}.json"


def product_facts(product_dir: Path) -> str:
    fingerprint = fact_source_fingerprint(product_dir)
    cache_path = fact_cache_path(product_dir)
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8-sig"))
            if (
                cached.get("version") == FACT_CACHE_VERSION
                and cached.get("source") == product_dir.relative_to(ROOT).as_posix()
                and cached.get("fingerprint") == fingerprint
                and isinstance(cached.get("facts"), str)
            ):
                return cached["facts"]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    chunks: list[str] = []
    for path in sorted(product_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in DOC_SUFFIXES:
            text = re.sub(r"\s+", " ", read_document(path)).strip()
            if text:
                chunks.append(f"{path.name}: {text}")
    facts = " ".join(chunks)
    segments = re.split(r"(?<=[。!?；;])\s+|\s{2,}", facts)
    unique: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        normalized = segment.strip(" ,;|")
        key = normalized.casefold()
        if normalized and key not in seen:
            unique.append(normalized)
            seen.add(key)
    compact = " ".join(unique)
    result = compact[:1800] or f"Product identifier: {product_dir.name}. Infer only visible appearance from references; do not invent specifications."
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "version": FACT_CACHE_VERSION,
        "source": product_dir.relative_to(ROOT).as_posix(),
        "fingerprint": fingerprint,
        "facts": result,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def prompt_safe_facts(facts: str, product_name: str) -> str:
    """Keep verified product facts while removing claims that must not reach positive image context."""
    # Compliance reports are evidence for internal review, not useful visual-generation context.
    facts = re.split(r"(?:IP44报告|IP\s*CODE\s*REPORT|Report\s+No\.)", facts, maxsplit=1, flags=re.IGNORECASE)[0]
    sentence_segments = re.split(r"(?<=[。!?；;])\s*", facts)
    segments = [f"Product identifier: {product_name}"]
    field_patterns = (
        r"(?:材料|灯罩材质|供电方式|灯体材料)[：:]\s*[^\s，。；;]+",
        r"父规格（品类）[：:]\s*(?:\d+件装\s*)+",
        r"电池容量[：:]\s*\d+\s*mah",
        r"是否需要组装[：:]\s*[^\s，。；;]+",
        r"(?:最长边|次长边|最短边)[：:]\s*\d+(?:\.\d+)?\s*cm",
        r"重量[：:]\s*\d+(?:\.\d+)?\s*g",
        r"色温[：:]\s*\d+(?:-\d+)?\s*K",
        r"LED灯[：:]\s*\d+颗\s*\d+(?:\.\d+)?W\s*[^；;\d]+(?:\(SMD\))?",
    )
    for field_pattern in field_patterns:
        segments.extend(match.group(0) for match in re.finditer(field_pattern, facts, flags=re.IGNORECASE))
    # Preserve each documented pack-size line with its dimensions and weight as one unambiguous fact.
    segments.extend(
        match.group(0)
        for match in re.finditer(
            r"[\u4e00-\u9fffA-Za-z0-9-]*\d+件装\s+[A-Za-z0-9-]+\s+最长边[：:]\s*[^，。；;]+，次长边[：:]\s*[^，。；;]+，最短边[：:]\s*[^，。；;]+，重量[：:]\s*[^，。；;]+",
            facts,
        )
    )
    segments.extend(
        match.group(0)
        for match in re.finditer(
            r"[\u4e00-\u9fffA-Za-z-]+\d+件装\s+[A-Za-z0-9-]+\s+最长边[：:]\s*\d+(?:\.\d+)?cm｜次长边[：:]\s*\d+(?:\.\d+)?cm｜最短边[：:]\s*\d+(?:\.\d+)?cm，重量[：:]\s*\d+(?:\.\d+)?g",
            facts,
        )
    )
    segments.extend(
        segment for segment in sentence_segments
        if len(segment) >= 20 and any(marker in segment for marker in ("安装", "夹子", "角度可调", "旋转", "材质", "材料"))
    )
    safe: list[str] = []
    for segment in segments:
        cleaned = re.sub(r"^\s*\d+[、.．]\s*", "", segment).strip(" ,;|。!")
        if not cleaned or any(re.search(pattern, cleaned, flags=re.IGNORECASE) for pattern in PROMPT_FACT_BLOCKLIST):
            continue
        if cleaned not in safe:
            safe.append(cleaned)
    compact = " ".join(safe)
    return compact[:1400] or f"Product identifier: {product_name}. Preserve only appearance visibly confirmed by the references."


def image_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def safe_reference_images(folder: Path) -> list[Path]:
    return [
        path
        for path in image_files(folder)
        if not any(token in path.name.lower() for token in ("fcc", "证书", "认证"))
    ]


def select_product_refs(product_dir: Path, limit: int = 5) -> list[Path]:
    images = safe_reference_images(product_dir)
    preferred = [p for p in images if "主图" in p.parts]
    others = [p for p in images if p not in preferred]
    return (preferred + others)[:limit]


def select_combo_refs(product_dir: Path, accessory_dir: Path) -> list[Path]:
    return select_product_refs(product_dir, 3) + safe_reference_images(accessory_dir)[:2]


def classify_scene_domain(facts: str) -> str:
    text = f" {facts.casefold()} "

    def contains(keyword: str) -> bool:
        normalized = keyword.casefold()
        if normalized.isascii():
            return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None
        return normalized in text

    scores = {
        domain: sum(1 for keyword in keywords if contains(keyword))
        for domain, keywords in SCENE_DOMAIN_KEYWORDS.items()
    }
    best_domain, best_score = max(scores.items(), key=lambda item: item[1])
    return best_domain if best_score > 0 else "neutral-commercial"


def scene_variant(product: str, accessory: str | None, facts: str) -> dict[str, str]:
    domain = classify_scene_domain(facts)
    key = f"{product}|{accessory or 'standalone'}|{domain}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    spaces = SCENE_DOMAINS[domain]
    space, context = spaces[int.from_bytes(digest[:2], "big") % len(spaces)]
    angle, composition, lighting, tone = VISUAL_VARIANTS[int.from_bytes(digest[2:4], "big") % len(VISUAL_VARIANTS)]
    return {
        "domain": domain,
        "space": space,
        "context": context,
        "angle": angle,
        "composition": composition,
        "lighting": lighting,
        "tone": tone,
    }


def interaction_plan(facts: str) -> dict[str, str]:
    lowered = facts.casefold()
    for _, keywords, policy in INTERACTION_POLICIES:
        if any(keyword.casefold() in lowered for keyword in keywords):
            return dict(policy)
    return dict(DEFAULT_INTERACTION_POLICY)


def shot_instruction(shot: str, combo: bool, variant: dict[str, str]) -> tuple[str, str, str]:
    if shot == "main":
        return (
            "clean commercial hero image",
            "Show the complete product as the dominant centered subject on a clean bright background with honest scale and no visual clutter.",
            "No headline is required; if a label is useful, use only a short neutral bundle label.",
        )
    if shot == "size":
        return (
            "clear dimension infographic",
            "Show the complete product and use precise thin measurement lines. Include only dimensions supported by the supplied product information or reference image; never guess.",
            "Use concise US English dimension labels such as Length, Width, Height, and Weight, followed by the exact supported values. Do not reproduce Chinese field names.",
        )
    if shot == "lifestyle-scene":
        return (
            "immersive bright-daytime lifestyle photography with low advertising noise and strong psychological ownership",
            f"Show the complete product naturally integrated into {variant['space']} during {variant['context']}. Let the environment communicate the product category within a glance and translate verified features into visible sensory comfort, order, texture, or ease. Use {variant['angle']}. Keep the product clearly readable as the key to the experience without making it look pasted in or theatrically staged.",
            "Use no text unless a tiny neutral label materially improves comprehension.",
        )
    if shot == "detail":
        return (
            "full-product feature explanation",
            "Keep the entire product visible and use restrained callout lines or small insets to explain verified materials, construction, surface, attachment, or handling features.",
            "Use only short factual feature labels supported by the supplied information.",
        )
    return (
        "ordered comparison infographic",
        "Keep the complete product visible and compare verified states, use cases, arrangement options, included quantity, or before-and-after presentation without attacking another product.",
        "Use short neutral comparison headings and only factual labels supported by supplied information.",
    )


def make_prompt(
    product: str,
    facts: str,
    shot: str,
    accessory: str | None = None,
    accessory_facts: str = "",
) -> dict[str, Any]:
    variant = scene_variant(product, accessory, facts)
    interaction = interaction_plan(facts)
    safe_facts = prompt_safe_facts(facts, product)
    safe_accessory_facts = prompt_safe_facts(accessory_facts, accessory or "accessory") if accessory else ""
    style, action, copy_rule = shot_instruction(shot, accessory is not None, variant)
    if shot == "lifestyle-scene":
        action += (
            f" Physical interaction is mandatory: {interaction['completed_state']}. "
            f"Support: {interaction['support_surface']}. Contact: {interaction['contact_points']}. "
            f"Load logic: {interaction['load_direction']}. Avoid: {interaction['forbidden_state']}."
        )
    environment = (
        f"Immersive but uncluttered {variant['space']} in bright daytime, expressing {variant['tone']}. Build a familiar, attainable slice of daily life with natural spatial depth, tactile material cues, and restrained lived-in details. The complete product remains immediately readable; no person is required, and the setting must never become dark, cinematic, or more visually important than the product."
        if shot == "lifestyle-scene"
        else "Bright, clean daytime environment appropriate to the verified product use; soft neutral background and restrained supporting props."
    )
    filename = f"{product}-{accessory + '-' if accessory else 'standalone-'}{shot}"
    if accessory:
        subject: dict[str, str] = {
            "main_product": f"{product}, shown in full and treated as the unmistakable visual core. Preserve its exact visible shape, count, color, proportions, and construction from the product references.",
            "accessory": f"{accessory}, shown in full as a smaller secondary add-on. Match its reference color, material, and shape exactly. Verified accessory information: {safe_accessory_facts[:700]}",
            "hierarchy": "Main product carries about 70% of visual weight through scale, central placement, sharper focus, and stronger illumination; accessory carries about 20% and remains quietly supportive.",
            "colors": "Match both reference sets exactly; do not shift accessory colors toward yellow, neon, or excessive saturation.",
            "materials": "Render only materials visibly supported by references and supplied facts, with realistic surface response.",
            "action": f"{action} Present both items in a natural relationship rather than as an arbitrary pile.",
            "condition": "Both products are new, clean, complete, undamaged, correctly proportioned, and free from distracting packaging.",
        }
        logic = (
            f"Create a credible low-noise pairing between {product} and {accessory}. If their direct functional relationship is weak, "
            "use a neutral coordinated retail bundle, tidy storage, gifting, or adjacent-use presentation; never invent compatibility or functionality."
        )
    else:
        subject = {
            "item": f"{product}, shown completely and matching all supplied references. It is the sole visual hero; preserve its verified count, dimensions, color, shape, material, and construction.",
            "colors": "Match reference colors exactly with natural commercial color accuracy.",
            "materials": "Show only materials and finishes supported by references or documentation.",
            "action": action,
            "condition": "New, clean, complete, undamaged, correctly proportioned, and free from packaging clutter.",
        }
        logic = "Standalone product image: communicate one clear commercial purpose while preserving the full product and all verified facts."

    prompt = {
        "filename": filename,
        "shot": "1:1 aspect ratio",
        "subject": subject,
        "combination_logic": logic,
        "product_information": safe_facts,
        "reference_image_policy": REFERENCE_POLICY,
        "diversity_plan": {
            "scene_domain": variant["domain"],
            "space_type": variant["space"] if shot == "lifestyle-scene" else "shot-specific controlled setting",
            "use_context": variant["context"] if shot == "lifestyle-scene" else style,
            "camera_angle": variant["angle"] if shot == "lifestyle-scene" else "selected for the requested information type",
            "composition_family": variant["composition"] if shot == "lifestyle-scene" else "distinct from the other four image types",
            "daylight_direction": variant["lighting"] if shot == "lifestyle-scene" else "bright diffused commercial daylight",
            "emotional_tone": variant["tone"] if shot == "lifestyle-scene" else "clear, factual, trustworthy",
        },
        "interaction_plan": interaction if shot == "lifestyle-scene" else {
            "interaction_mode": "not required for this shot",
            "completed_state": "preserve complete, stable, physically credible product presentation",
        },
        "environment": environment,
        "composition": (variant["composition"] + "; keep complete items inside frame with safe margins and no obstructive overlap.") if shot == "lifestyle-scene" else "Stable square composition with the complete product fully inside frame, generous safe margins, no cropped edges, no overlap that hides included parts.",
        "camera": {"focal_length": "50-70mm equivalent", "aperture": "f/8", "angle": variant["angle"] if shot == "lifestyle-scene" else "clear three-quarter or straight-on product-readable angle"},
        "lighting": variant["lighting"] if shot == "lifestyle-scene" else "Bright diffused daylight with soft commercial fill, accurate reflections, controlled highlights, and clearly separated edges.",
        "color_grade": "Neutral, bright, realistic e-commerce grade with clean whites and restrained saturation.",
        "style": style,
        "quality": "High-resolution photorealistic e-commerce image, accurate geometry, legible information, crisp but natural materials.",
        "negatives": COMPLIANCE,
        "text": {
            "Content": f"{US_ENGLISH_TEXT_POLICY} {copy_rule}",
            "Position": "Use a quiet top or side information zone without covering either product.",
            "font_style": "Clean sans-serif",
            "font_color": "High-contrast neutral charcoal with one restrained accent color",
            "font_size": "Large enough to read at marketplace thumbnail scale, with limited word count",
            "Effects": "Thin guide lines and subtle solid backing only; no glow, dramatic shadow, badge, leaf icon, or certification-like symbol.",
        },
    }
    if product == "C315XCJLY":
        topology = (
            "C315 topology is mandatory: there is exactly one elongated main camera body with two opposite faces, not two main units. "
            "The cabin-facing face has one central rectangular display with one square camera module at the left end and one at the right end. "
            "The road-facing opposite face has one central forward camera lens and no display. The display and central forward lens can never appear on the same physical face. "
            "A separate small cube rear-camera module is installed independently at the rear window; it is never fused to the main body or allowed to float."
        )
        identity_field = "main_product" if accessory else "item"
        prompt["subject"][identity_field] += f" {topology}"
        prompt["product_information"] = f"Product identifier: C315XCJLY. {topology}"
        prompt["reference_image_policy"] += (
            " When a reference shows cabin-facing and road-facing views together, treat them as two views of the same single main body, never as simultaneous surfaces, separate included main units, or parts to merge."
        )
        prompt["negatives"] += (
            " No display and central forward lens on the same face; no duplicated main body; no fused front/back surfaces; no extra end cameras; no rear camera attached to the main body."
        )
        if shot == "lifestyle-scene":
            prompt["subject"]["action"] += (
                " Use a cabin-interior viewpoint: show only the cabin-facing side of the installed main body, with the central display between the left and right square camera modules. "
                "The central forward lens is on the hidden opposite side facing through the windshield and must not be visible. "
                "Place the separate cube rear camera on the rear window in credible cabin depth, or omit a close view of it rather than floating it beside the main unit."
            )
            prompt["interaction_plan"].update({
                "contact_points": "the suction mount sits flush against the windshield; the single main body is fully seated below it with its display facing the cabin and its hidden forward lens facing the road",
                "completed_state": "show exactly one main body correctly mounted: cabin-facing display in the center, square left and right camera modules on that same cabin-facing side, and the forward lens hidden on the opposite road-facing side",
                "forbidden_state": "never show the central display and central forward lens on one face; no duplicated main body, fused opposite surfaces, floating rear camera, fake windshield contact, blocked driver sightline, or invented holder",
            })
    return prompt


def write_prompts(job_dir: Path, prompts: list[dict[str, Any]], refresh: bool) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "参考图").mkdir(exist_ok=True)
    for folder in SHOT_FOLDERS.values():
        (job_dir / folder).mkdir(exist_ok=True)
    json_path = job_dir / "prompts.json"
    if json_path.exists() and not refresh:
        return
    json_path.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
    blocks = [f"{index}.\n```json\n{json.dumps(prompt, ensure_ascii=False, indent=2)}\n```" for index, prompt in enumerate(prompts, 1)]
    (job_dir / "prompts.md").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def validate_prompt_set(prompts: list[dict[str, Any]], combo: bool) -> None:
    if len(prompts) != len(SHOT_FOLDERS):
        raise ValueError(f"Expected {len(SHOT_FOLDERS)} prompts, got {len(prompts)}")

    filenames = [str(prompt.get("filename") or "") for prompt in prompts]
    if len(set(filenames)) != len(filenames):
        raise ValueError("Prompt filenames must be unique within each five-image set")

    for shot in SHOT_FOLDERS:
        matching = [prompt for prompt in prompts if str(prompt.get("filename") or "").endswith(shot)]
        if len(matching) != 1:
            raise ValueError(f"Expected exactly one prompt ending in {shot}, got {len(matching)}")

    required = {
        "filename", "shot", "subject", "product_information", "reference_image_policy",
        "diversity_plan", "environment", "composition", "camera", "lighting", "style",
        "quality", "negatives", "text",
    }
    for prompt in prompts:
        missing = sorted(required - prompt.keys())
        if missing:
            raise ValueError(f"Prompt {prompt.get('filename')} missing fields: {', '.join(missing)}")
        if len(json.dumps(prompt, ensure_ascii=False)) < 1500:
            raise ValueError(f"Prompt {prompt.get('filename')} is too thin for reliable generation")
        if "identity constraint" not in str(prompt["reference_image_policy"]):
            raise ValueError(f"Prompt {prompt.get('filename')} lacks a strong reference-image boundary")
        product_information = str(prompt["product_information"])
        blocked = [pattern for pattern in PROMPT_FACT_BLOCKLIST if re.search(pattern, product_information, flags=re.IGNORECASE)]
        if blocked:
            raise ValueError(f"Prompt {prompt.get('filename')} contains sensitive positive-context product information")
        if combo:
            subject = prompt["subject"]
            for field in ("main_product", "accessory", "hierarchy"):
                if not str(subject.get(field) or "").strip():
                    raise ValueError(f"Combination prompt {prompt.get('filename')} lacks subject.{field}")

    scene = next(prompt for prompt in prompts if str(prompt["filename"]).endswith("lifestyle-scene"))
    diversity = scene.get("diversity_plan") or {}
    for field in ("scene_domain", "space_type", "use_context", "camera_angle", "composition_family", "daylight_direction"):
        if not str(diversity.get(field) or "").strip():
            raise ValueError(f"Scene prompt {scene.get('filename')} lacks diversity_plan.{field}")
    interaction = scene.get("interaction_plan") or {}
    for field in ("interaction_mode", "support_surface", "contact_points", "load_direction", "completed_state", "forbidden_state"):
        if not str(interaction.get(field) or "").strip():
            raise ValueError(f"Scene prompt {scene.get('filename')} lacks interaction_plan.{field}")


def products(selected: str | None) -> list[Path]:
    if selected:
        result = SOURCE_ROOT / selected
        if not result.is_dir():
            raise ValueError(f"Product source folder does not exist: {result}")
        return [result]
    return sorted(path for path in SOURCE_ROOT.iterdir() if path.is_dir())


def prepare(
    product_dirs: list[Path],
    refresh: bool,
    selected_tasks: list[str] | None = None,
    combinations_only: bool = False,
    variants_only: bool = False,
) -> None:
    accessories = sorted(path for path in ACCESSORY_ROOT.iterdir() if path.is_dir())
    requested = set(selected_tasks or [])
    known_tasks = {"单品", *(path.name for path in accessories)}
    unknown = {task for task in requested - known_tasks if not task.startswith("单品-")}
    if unknown:
        raise ValueError(f"Unknown prepare task(s): {', '.join(sorted(unknown))}")
    for product_dir in product_dirs:
        facts = product_facts(product_dir)
        product_out = OUTPUT_ROOT / product_dir.name
        prepared_jobs = 0
        if not combinations_only and not variants_only and (not requested or "单品" in requested):
            standalone = [make_prompt(product_dir.name, facts, shot) for shot in SHOT_FOLDERS]
            validate_prompt_set(standalone, combo=False)
            write_prompts(product_out / "单品", standalone, refresh)
            prepared_jobs += 1
        for accessory_dir in accessories:
            if variants_only:
                continue
            if requested and accessory_dir.name not in requested:
                continue
            accessory_facts = product_facts(accessory_dir)
            combo = [make_prompt(product_dir.name, facts, shot, accessory_dir.name, accessory_facts) for shot in SHOT_FOLDERS]
            validate_prompt_set(combo, combo=True)
            write_prompts(product_out / accessory_dir.name, combo, refresh)
            prepared_jobs += 1
        print(f"prepared product={product_dir.name} jobs={prepared_jobs} prompts={5 * prepared_jobs}")


def museforge_run_config() -> MuseForgeRunConfig:
    """Read the backend-controlled staging contract without exposing a CLI path."""
    raw_run_dir = os.getenv("MUSEFORGE_RUN_DIR", "").strip()
    raw_variants = os.getenv("MUSEFORGE_VARIANTS", "").strip()
    try:
        variants = int(raw_variants or "1")
    except ValueError as exc:
        raise ValueError("MUSEFORGE_VARIANTS must be an integer from 1 to 6") from exc
    if not 1 <= variants <= 6:
        raise ValueError("MUSEFORGE_VARIANTS must be from 1 to 6")

    run_dir: Path | None = None
    if raw_run_dir:
        supplied = Path(raw_run_dir)
        if not supplied.is_absolute():
            raise ValueError("MUSEFORGE_RUN_DIR must be an absolute path")
        run_dir = supplied.resolve()
    elif variants != 1:
        raise ValueError("MUSEFORGE_RUN_DIR is required when MUSEFORGE_VARIANTS is greater than 1")

    return MuseForgeRunConfig(
        run_id=os.getenv("MUSEFORGE_RUN_ID", "").strip() or None,
        run_dir=run_dir,
        variants=variants if run_dir else 1,
    )


def museforge_run_spec(run: MuseForgeRunConfig) -> dict[str, Any]:
    """Load the immutable backend-authored canvas direction for this run."""
    raw_path = os.getenv("MUSEFORGE_RUN_SPEC_PATH", "").strip()
    if not raw_path:
        return {}
    if run.run_dir is None:
        raise ValueError("MUSEFORGE_RUN_SPEC_PATH is only valid for staged runs")
    path = Path(raw_path)
    if not path.is_absolute():
        raise ValueError("MUSEFORGE_RUN_SPEC_PATH must be an absolute path")
    path = path.resolve()
    try:
        path.relative_to(run.run_dir.resolve())
    except ValueError as exc:
        raise ValueError("MUSEFORGE_RUN_SPEC_PATH escaped MUSEFORGE_RUN_DIR") from exc
    if path != (run.run_dir / "run-spec.json").resolve() or not path.is_file():
        raise ValueError("MuseForge run spec is unavailable")
    if path.stat().st_size > MUSEFORGE_RUN_SPEC_MAX_BYTES:
        raise ValueError("MuseForge run spec is unexpectedly large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("MuseForge run spec has an unsupported format")
    version = value.get("version")
    if type(version) is not int or version not in MUSEFORGE_RUN_SPEC_VERSIONS:
        raise ValueError("MuseForge run spec has an unsupported version")
    schema = value.get("schema")
    if schema is not None and schema != MUSEFORGE_RUN_SPEC_SCHEMA:
        raise ValueError("MuseForge run spec has an unsupported schema")
    if run.run_id and value.get("run_id") != run.run_id:
        raise ValueError("MuseForge run spec does not match the active run")
    return value


def museforge_run_items(run_spec: dict[str, Any]) -> set[tuple[str, str]] | None:
    """Return an optional sparse task/shot scope from a v2 run spec."""
    raw_items = run_spec.get("items")
    if raw_items is None:
        return None
    if not isinstance(raw_items, list):
        raise ValueError("MuseForge run spec items must be a list")
    declared_tasks = run_spec.get("tasks", [])
    declared_shots = run_spec.get("shots", [])
    if not isinstance(declared_tasks, list) or not isinstance(declared_shots, list):
        raise ValueError("MuseForge run spec tasks and shots must be lists")
    items: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise ValueError(f"MuseForge run spec items[{index}] must be an object")
        task = raw_item.get("task")
        shot = raw_item.get("shot")
        if not isinstance(task, str) or not task or not isinstance(shot, str) or not shot:
            raise ValueError(
                f"MuseForge run spec items[{index}] requires string task and shot"
            )
        pair = (task, shot)
        if pair in items:
            raise ValueError(f"MuseForge run spec contains duplicate item {task}/{shot}")
        if task not in declared_tasks or shot not in declared_shots:
            raise ValueError(
                f"MuseForge run spec item {task}/{shot} is outside tasks/shots"
            )
        items.add(pair)
    return items


def apply_canvas_creative_brief(
    prompt: dict[str, Any],
    brief: dict[str, Any],
) -> dict[str, Any]:
    """Add canvas direction without replacing verified facts or safety policies."""
    clean = {
        key: str(brief.get(key) or "").strip()
        for key in ("subject", "environment", "composition", "negatives", "visible_text")
    }
    if re.search(r"[\u3400-\u9fff]", clean["visible_text"]):
        raise ValueError(
            "MuseForge canvas visible text must use marketplace-ready English copy"
        )
    for field in ("subject", "environment", "composition", "visible_text"):
        if any(
            re.search(pattern, clean[field], flags=re.IGNORECASE)
            for pattern in PROMPT_FACT_BLOCKLIST
        ):
            raise ValueError(
                f"MuseForge canvas direction {field} contains an unsupported positive claim"
            )
    if not any(clean.values()):
        return prompt
    result = copy.deepcopy(prompt)
    result["museforge_canvas_direction"] = {
        "policy": "Additional art direction only. Verified product facts, reference boundaries, physical constraints, and compliance rules remain authoritative.",
        **clean,
    }
    if clean["environment"]:
        result["environment"] = (
            f"{result.get('environment', '')} Additional canvas environment direction: "
            f"{clean['environment']}"
        ).strip()
    if clean["composition"]:
        result["composition"] = (
            f"{result.get('composition', '')} Additional canvas composition direction: "
            f"{clean['composition']}"
        ).strip()
    if clean["negatives"]:
        result["negatives"] = (
            f"{result.get('negatives', '')} Additional exclusions from the canvas: "
            f"{clean['negatives']}"
        ).strip()
    if clean["visible_text"]:
        text_policy = result.get("text") if isinstance(result.get("text"), dict) else {}
        text_policy = dict(text_policy)
        text_policy["Content"] = (
            f"{text_policy.get('Content', '')} Canvas-visible copy request: "
            f"{clean['visible_text']}"
        ).strip()
        result["text"] = text_policy
    return result


def output_path(job_dir: Path, prompt: dict[str, Any]) -> Path:
    filename = str(prompt["filename"])
    shot = next((key for key in SHOT_FOLDERS if filename.endswith(key)), "main")
    extension = image_output_format()
    return job_dir / SHOT_FOLDERS[shot] / f"{filename}.{extension}"


def staged_output_path(
    run_dir: Path,
    *,
    product: str,
    task: str,
    shot: str,
    candidate_index: int,
) -> Path:
    """Build one immutable candidate path below the validated run directory."""
    extension = image_output_format()
    target = (run_dir / product / task / shot / f"candidate-{candidate_index:02d}.{extension}").resolve()
    try:
        target.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("Staged image target escaped MUSEFORGE_RUN_DIR") from exc
    return target


def event_relative_path(path: Path, run_dir: Path | None) -> tuple[str, str]:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix(), "workspace"
    except ValueError:
        if run_dir is not None:
            try:
                return resolved.relative_to(run_dir.resolve()).as_posix(), "run_dir"
            except ValueError:
                pass
    return path.name, "filename"


def emit_museforge_event(event_type: str, **payload: Any) -> None:
    event: dict[str, Any] = {"v": 1, "type": event_type}
    run_id = os.getenv("MUSEFORGE_RUN_ID", "").strip()
    if run_id:
        event["run_id"] = run_id
    event.update(payload)
    print(
        MUSEFORGE_EVENT_PREFIX + json.dumps(event, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def is_standalone_task(task_name: str) -> bool:
    return task_name == "单品" or task_name.startswith("单品-")


def curated_reference_images(job_dir: Path, combo: bool, shot: str | None = None) -> list[Path]:
    reference_dir = job_dir / "参考图"
    manifest_path = job_dir / "reference_manifest.json"
    refs = image_files(reference_dir)
    if not refs:
        raise ValueError(f"No AI-curated references in {reference_dir.relative_to(ROOT)}")
    if not manifest_path.is_file():
        raise ValueError(f"Missing curated-reference manifest: {manifest_path.relative_to(ROOT)}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Invalid reference manifest {manifest_path.relative_to(ROOT)}: {exc}") from exc
    entries = manifest.get("references") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"Reference manifest must contain a references list: {manifest_path.relative_to(ROOT)}")
    actual_names = {path.name for path in refs}
    manifest_names = {str(entry.get("filename") or "") for entry in entries if isinstance(entry, dict)}
    if actual_names != manifest_names:
        raise ValueError(f"Reference manifest does not match files in {reference_dir.relative_to(ROOT)}")
    main_refs = [path for path in refs if path.name.startswith("主商品-")]
    accessory_refs = [path for path in refs if path.name.startswith("配件-")]
    if len(main_refs) == 0 or len(main_refs) > (3 if combo else 5):
        raise ValueError(f"Invalid main-product reference count in {reference_dir.relative_to(ROOT)}")
    if combo:
        if len(accessory_refs) == 0 or len(accessory_refs) > 2:
            raise ValueError(f"Invalid accessory reference count in {reference_dir.relative_to(ROOT)}")
        if len(refs) > 5:
            raise ValueError(f"Too many curated references in {reference_dir.relative_to(ROOT)}")
    elif accessory_refs or len(refs) > 5:
        raise ValueError(f"Standalone references must contain only up to five 主商品-* files in {reference_dir.relative_to(ROOT)}")
    if len(main_refs) + len(accessory_refs) != len(refs):
        raise ValueError(f"Every curated reference must use a 主商品-* or 配件-* filename in {reference_dir.relative_to(ROOT)}")
    all_refs = main_refs + accessory_refs
    if not shot:
        return all_refs

    entries_by_name = {
        str(entry.get("filename") or ""): entry
        for entry in entries
        if isinstance(entry, dict)
    }
    has_routing = any(isinstance(entry.get("selected_for"), list) for entry in entries_by_name.values())
    if not has_routing:
        return all_refs

    selected = [
        path for path in all_refs
        if shot in entries_by_name.get(path.name, {}).get("selected_for", [])
    ]

    # Every request needs an identity anchor. Combination requests additionally need an accessory anchor.
    if not any(path.name.startswith("主商品-") for path in selected):
        selected.insert(0, main_refs[0])
    if combo and not any(path.name.startswith("配件-") for path in selected):
        selected.append(accessory_refs[0])
    return list(dict.fromkeys(selected))


def collect_jobs(
    product_dirs: list[Path],
    overwrite: bool,
    selected_tasks: list[str] | None = None,
    combinations_only: bool = False,
    variants_only: bool = False,
    selected_shots: list[str] | None = None,
) -> list[GenerationJob]:
    jobs: list[GenerationJob] = []
    run = museforge_run_config()
    run_spec = museforge_run_spec(run)
    run_items = museforge_run_items(run_spec) if run_spec else None
    creative_brief = run_spec.get("creative_brief", {})
    if not isinstance(creative_brief, dict):
        raise ValueError("MuseForge creative brief must be an object")
    requested = set(selected_tasks or [])
    requested_shots = set(selected_shots or [])
    for product_dir in product_dirs:
        if run_spec and product_dir.name != run_spec.get("product"):
            raise ValueError("MuseForge run spec product does not match the requested product")
        product_out = OUTPUT_ROOT / product_dir.name
        available = {path.name for path in product_out.iterdir() if path.is_dir() and (path / "prompts.json").exists()}
        missing = requested - available
        if missing:
            raise ValueError(f"Unknown task(s) for {product_dir.name}: {', '.join(sorted(missing))}")
        for job_dir in sorted(
            path for path in product_out.iterdir()
            if path.is_dir()
            and (path / "prompts.json").exists()
            and (not combinations_only or not is_standalone_task(path.name))
            and (not variants_only or path.name.startswith("单品-"))
            and (not requested or path.name in requested)
        ):
            prompts = read_prompt_objects(job_dir / "prompts.json")
            validate_prompt_set(prompts, combo=not is_standalone_task(job_dir.name))
            for index, prompt in enumerate(prompts):
                shot = next((key for key in SHOT_FOLDERS if str(prompt["filename"]).endswith(key)), "main")
                if requested_shots and shot not in requested_shots:
                    continue
                if run_spec:
                    if job_dir.name not in run_spec.get("tasks", []):
                        raise ValueError("MuseForge run spec task does not match the requested task")
                    if shot not in run_spec.get("shots", []):
                        raise ValueError("MuseForge run spec shot does not match the requested shot")
                    if run_items is not None and (job_dir.name, shot) not in run_items:
                        continue
                    prompt = apply_canvas_creative_brief(prompt, creative_brief)
                if run.run_dir is not None:
                    candidate_targets = [
                        (
                            candidate_index,
                            staged_output_path(
                                run.run_dir,
                                product=product_dir.name,
                                task=job_dir.name,
                                shot=shot,
                                candidate_index=candidate_index,
                            ),
                        )
                        for candidate_index in range(1, run.variants + 1)
                    ]
                else:
                    candidate_targets = [(1, output_path(job_dir, prompt))]

                pending_targets = [
                    (candidate_index, target)
                    for candidate_index, target in candidate_targets
                    # Staged candidates are immutable. --overwrite continues to
                    # apply only to the legacy formal-output workflow.
                    if (
                        (run.staged and not target.exists())
                        or (not run.staged and (overwrite or not target.exists()))
                    )
                ]
                if not pending_targets:
                    continue
                refs = curated_reference_images(
                    job_dir,
                    combo=not is_standalone_task(job_dir.name),
                    shot=shot,
                )
                for candidate_index, target in pending_targets:
                    jobs.append(GenerationJob(
                        product=product_dir.name,
                        task=job_dir.name,
                        shot=shot,
                        candidate_index=candidate_index,
                        job_dir=job_dir,
                        target=target,
                        refs=refs,
                        prompt=prompt,
                        prompt_index=index,
                        prompt_total=len(prompts),
                        run_dir=run.run_dir,
                        allow_overwrite=overwrite if not run.staged else False,
                    ))
    return jobs


def preview_jobs(jobs: list[GenerationJob]) -> None:
    print(f"missing_images={len(jobs)}")
    for job in jobs:
        display_path, _ = event_relative_path(job.target, job.run_dir)
        print(f"  refs={len(job.refs)} -> {display_path}")


def execute_generation_job(job: GenerationJob) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = request_image(
            combo_dir=job.job_dir,
            refs=job.refs,
            job=job.prompt,
            index=job.prompt_index,
            total=job.prompt_total,
            output_path=job.target,
            allow_overwrite=job.allow_overwrite,
        )
        result.setdefault("elapsed_seconds", round(time.perf_counter() - started, 2))
        return result
    except Exception as exc:
        return {
            "status": "failed",
            "error": str(exc),
            "path": str(job.target),
            "estimated_cost": 0.0,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }


def candidate_event_payload(job: GenerationJob, result: dict[str, Any]) -> dict[str, Any]:
    relative_path, relative_to = event_relative_path(job.target, job.run_dir)
    cost = float(result.get("estimated_cost", 0.0) or 0.0)
    elapsed = float(result.get("elapsed_seconds", 0.0) or 0.0)
    return {
        "item_key": f"{job.product}/{job.task}/{job.shot}/{job.candidate_index:02d}",
        "product": job.product,
        "task": job.task,
        "shot": job.shot,
        "candidate_index": job.candidate_index,
        "relative_path": relative_path,
        "relative_to": relative_to,
        "filename": job.target.name,
        "prompt_filename": str(job.prompt.get("filename") or ""),
        "cost": cost,
        "estimated_cost": cost,
        "elapsed": elapsed,
        "elapsed_seconds": elapsed,
        "currency": result.get("currency", os.getenv("IMAGE_COST_CURRENCY", "USD")),
        "model": result.get("model", os.getenv("IMAGE_MODEL", "gpt-image-2")),
        "size": result.get("size", os.getenv("IMAGE_SIZE", "1024x1024")),
        "quality": result.get("quality", os.getenv("IMAGE_QUALITY", "low")),
        "reference_image_count": result.get("reference_image_count", len(job.refs)),
        "provider_channel_id": result.get(
            "provider_channel_id", os.getenv("MUSEFORGE_PROVIDER_CHANNEL_ID", "legacy")
        ),
        "provider_channel_name": result.get(
            "provider_channel_name",
            os.getenv("MUSEFORGE_PROVIDER_CHANNEL_NAME", "本地环境兼容渠道"),
        ),
        "provider_routing_mode": result.get(
            "provider_routing_mode",
            os.getenv("MUSEFORGE_PROVIDER_ROUTING_MODE", "legacy"),
        ),
    }


def generate(jobs: list[GenerationJob], concurrency: int) -> None:
    run = museforge_run_config()
    emit_museforge_event(
        "plan",
        total=len(jobs),
        total_items=len(jobs),
        variants=run.variants,
        staged=run.staged,
    )
    if not jobs:
        print("nothing to generate")
        return
    print(f"generating={len(jobs)} global_concurrency={concurrency}")
    saved = failed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(execute_generation_job, job): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            result = future.result()
            event_payload = candidate_event_payload(job, result)
            display_path = event_payload["relative_path"]
            if result["status"] in {"saved", "skipped"}:
                saved += result["status"] == "saved"
                emit_museforge_event(
                    "item.saved",
                    **event_payload,
                    reused=result["status"] == "skipped",
                )
                print(f"{result['status']} {display_path}")
            else:
                failed += 1
                emit_museforge_event(
                    "item.failed",
                    **event_payload,
                    error=str(result.get("error") or "Unknown image generation failure"),
                )
                print(f"failed {display_path}: {result.get('error')}")
    print(f"summary saved={saved} failed={failed}")
    if failed:
        raise RuntimeError(f"{failed} image requests failed")


def main() -> None:
    args = parse_args()
    load_mixed_env(ENV_PATH)
    product_dirs = products(args.product)
    if args.combinations_only and args.variants_only:
        raise ValueError("--combinations-only and --variants-only are mutually exclusive")
    if args.combinations_only and args.task and "单品" in args.task:
        raise ValueError("--combinations-only cannot be combined with --task 单品")
    if args.action == "prepare":
        prepare(
            product_dirs,
            args.refresh_prompts,
            args.task,
            args.combinations_only,
            args.variants_only,
        )
        return
    if args.overwrite:
        os.environ["IMAGE_OVERWRITE"] = "true"
    overwrite = args.overwrite or parse_bool(os.getenv("IMAGE_OVERWRITE"))
    jobs = collect_jobs(product_dirs, overwrite, args.task, args.combinations_only, args.variants_only, args.shot)
    if args.action == "preview":
        preview_jobs(jobs)
        return
    concurrency = args.concurrency or int(os.getenv("IMAGE_COMBO_CONCURRENCY", "10"))
    generate(jobs, max(1, min(10, concurrency)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
