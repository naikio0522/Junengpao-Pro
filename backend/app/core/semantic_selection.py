"""Plan a spoken, single-product cut from time-aligned source transcript cues.

This module only selects existing source ranges.  Transcription, rendering and
visual review live elsewhere.  An optional analyzer may add per-unit semantic
labels; it cannot change source text or timecodes.  Selection is deterministic
and raises SemanticSelectionError when the evidence is insufficient.
"""

from __future__ import annotations

import math
import heapq
import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any


ROLES = {"hook", "problem", "demonstrate", "explain", "product", "cta"}
ROLE_STAGE = {"hook": 0, "problem": 1, "demonstrate": 2, "explain": 2,
              "product": 2, "cta": 3}
MIDDLE_ROLES = {"problem", "demonstrate", "explain"}
END_PUNCTUATION = re.compile(r"[。！？!?；;][”’\"']?$|(?<!\d)\.(?!\d)$")
CONTINUATION = re.compile(
    r"^(?:因为|所以|因此|但是|不过|然而|然后|而且|并且|同时|另外|还有|"
    r"这个|这款|这些|这种|它|他们|她们|就|也|再|更重要|换句话说|那么|如果)")
NEW_SENTENCE_START = re.compile(
    r"^(?:其实|接下来|现在|关键是|重点是|还在犹豫|情况严重|建议大家|我|你们|大家|朋友们|不用|不要|先|直接)")
DANGLING_END = re.compile(
    r"(?:的|地|得|把|被|给|让|用|是|会|能|要|在|和|与|或|因为|所以|但是|"
    r"如果|比如|包括|里面|这个|这款|它|一个|一款|一种|就是|叫做)$")
FINAL_PARTICLE = re.compile(r"(?:啊|呀|呢|吧|吗|嘛|啦|哦|呗|哟|了)$")
SENTENCE_VERB = re.compile(
    r"(?:有|没有|是|不是|用|刷|挤|选|看|买|知道|担心|出现|改善|容易|"
    r"需要|可以|能够|应该|建议|推荐|含有|添加|适合|保护|护理|清洁|够|"
    r"解决|说明|沾|敏感|口臭|口气)")
COMPLETE_INTENT = re.compile(
    r"^(?:(?:真的|好|行)[啊呀]?[，,\s]*)?(?:那|这|要不|这样)?"
    r"(?:我|我们|你|你们|咱们).{0,18}"
    r"(?:去|来|赶紧|马上|立刻)(?:抢|买|拍|拿|试|看|问|下单)$")
TIME_SENSITIVE = re.compile(
    r"(?:618|双\s*11|双十一|双\s*12|双十二|国庆|中秋|春节|元旦|周年庆|"
    r"CIE|今天|今晚|本场|现在下单|限时|倒计时|破[价架]|到手价|"
    r"最后\s*[\d一二三四五六七八九十两]+\s*单|仅剩|补贴|"
    r"(?:送|赠)\s*(?:你)?\s*[\d一二三四五六七八九十两]+\s*(?:支|件|盒|份|个|包)?|"
    r"买\s*一\s*送\s*一|\d+(?:\.\d+)?\s*(?:元|块|折|件)|"
    r"一\s*发\s*[\d一二三四五六七八九十两]+)", re.IGNORECASE)
EARLY_PURCHASE = re.compile(
    r"(?:一\s*发\s*[\d一二三四五六七八九十两]+|"
    r"(?:赶紧|抓紧|立刻|马上)?\s*去\s*抢(?!救)|"
    r"(?:下单|购买|点头像|进直播间|商品卡))")
ASR_PURCHASE = re.compile(r"(?:一\s*发\s*酒|抓(?:紧|进|你)?去墙|多(?:纯|吞)点)")
ASR_PURCHASE_END = re.compile(
    r"(?:我|我们|咱们).{0,18}(?:去(?:抢|墙)|多(?:囤|纯|吞)点)$")
VOICE_START_PRODUCT = re.compile(r"^(?:它|这个|这款|这支|这些|这种|就这个)")
VOICE_START_CAUSE = re.compile(r"^(?:所以|因此|这就是为什么|正因为)")
EXPLICIT_PRODUCT = re.compile(r"(?:俊小白|JXB[-_ ]?\d+|nHAP[-_ ]?Pro)", re.IGNORECASE)
ANALYZER_FIELDS = {"role", "topic_id", "pain_id", "mechanism_id", "introduces",
                   "requires", "claim_key", "take_group", "quality_score"}
SKU_ALIASES = {
    "JXB-COLOR": (r"色修", r"mHAP[-_ ]?White"),
    "JXB-BREATH": (r"口干口臭专健", r"口气款", r"口臭款"),
    "JXB-ENAMEL-GUM": (r"釉龈双护", r"釉龈"),
    "JXB-KIDS-MIXED": (r"换牙期", r"6\s*[-–到]\s*12岁"),
    "JXB-KIDS-TEEN": (r"青少年", r"恒牙期", r"12\s*[-–到]\s*18岁"),
    "JXB-KIDS-PRIMARY": (r"乳牙期", r"1\s*[-–到]\s*6岁"),
    "JXB-99": (r"nHAP[-_ ]?Pro", r"99\s*\+?\s*(?:管|修护|牙膏)",
               r"99\s*%\s*(?:纯度|原料)",
               r"亮皓密集修护", r"密集修护"),
}
TOPIC_ALIASES = {
    "breath": (r"口气", r"口臭", r"口干口臭", r"异味"),
    "whitening": (r"美白", r"色修", r"亮白", r"黄牙"),
    "sensitivity": (r"敏感", r"冷热酸甜", r"倒牙", r"酸痛"),
    "gum": (r"牙龈", r"釉龈"),
    "kids_age": (r"乳牙期", r"换牙期", r"青少年", r"恒牙期"),
    "usage": (r"挤膏", r"挤牙膏", r"用量", r"怎么用", r"用法", r"刷牙",
              r"牙刷", r"沾水", r"干刷", r"使用方法", r"不用.{0,3}太多",
              r"就这么一点点"),
}
PAIN_ALIASES = {
    "tooth_sensitivity": (r"敏感", r"冷热酸甜", r"倒牙", r"发酸", r"发软", r"酸痛", r"牙本质暴露"),
    "gum_recession": (r"牙龈.{0,3}(?:萎缩|退缩|退下去)", r"牙根.{0,3}露", r"露牙根"),
    "gum_bleeding": (r"牙龈.{0,3}(?:出血|红肿|肿痛)", r"刷牙.{0,3}出血"),
    "oral_odor_dryness": (r"口气", r"口臭", r"异味", r"口干", r"嘴巴.{0,3}干",
                           r"嘴里.{0,3}(?:有味|有异味)", r"嘴巴.{0,3}有味"),
    "tooth_discoloration": (r"黄牙", r"牙黄", r"牙齿.{0,4}(?:发黄|变黄|色渍)", r"牙渍"),
    "cavity_risk": (r"蛀牙", r"龋齿", r"防蛀"),
}
MECHANISM_ALIASES = {
    "mineral_repair": (r"羟基磷灰石", r"纳米.{0,4}磷灰石", r"封堵.{0,6}牙小管",
                       r"修(?:复|护).{0,6}牙釉质", r"再矿化", r"矿化修护", r"nHAP[-_ ]?Pro"),
    "color_correction": (r"色修", r"动态显白", r"光学.{0,4}(?:调色|增白)", r"紫色.{0,6}中和"),
    "dry_brushing": (r"(?:不|别|无需).{0,3}沾水", r"直接干刷", r"干刷"),
    "enamel_anti_cavity": (r"(?:钙磷|DCPD).{0,8}(?:护釉|防蛀)", r"护釉防蛀"),
    "protein_gum_care": (r"蛋膜肽", r"Zel['’]?ner"),
}
SPOKEN_SKU_PATTERNS = {sku: tuple(patterns) for sku, patterns in SKU_ALIASES.items()}
ASR_COLOR_SUSPECT = re.compile(r"(?:四球|色球|社羞|色羞|四修|社修|射修|涉修|射胸)")
ASR_COLOR_CONTEXT = re.compile(
    r"(?:牙膏|雅膏|美白|黄牙|黃牙|紫色|紙色|去黄|去黃|色渍|刷牙|牙齿|牙齒|泡沫|干刷|乾刷)")
_RETAKE = re.compile(r"(?:^|[，,。！？!?\s])(?:三二一|3\s*2\s*1|重来|再来一遍|这一条不要|卡了)(?:$|[，,。！？!?\s])")
_PRONOUN_START = re.compile(r"^(?:它|这个成分|这种成分|这款|这支|就这个)")
_ADDITIVE_START = re.compile(r"^(?:另外|还有|而且|并且|同时|其次)")
_SEQUENCE_START = re.compile(r"^(?:然后|接下来|再)")
_CONTRAST_START = re.compile(r"^(?:但是|不过|然而)")
MAX_HOOK_UNTRANSCRIBED_EDGE_S = 3.0


class SemanticSelectionError(ValueError):
    """A cut cannot be planned without guessing at content or ordering."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def infer_sku_id(path: str, transcript: str | Iterable[Mapping[str, Any]] = "") -> str | None:
    """Return one evidenced product identity, or None for unknown/mixed products.

    Generic symptom words in speech are not product identities.  The weak
    filename-only ``修护`` alias is useful for this project's 99 source files,
    but cannot override a conflicting explicit product name.  A comparison of
    several colours/products is intentionally ambiguous for a single-SKU cut.
    """
    if isinstance(transcript, str):
        spoken = transcript
    else:
        spoken = " ".join(str(cue.get("text") or "") for cue in transcript)
    normalized_path = path.replace("\\", "/").rsplit("/", 2)[-2:]
    normalized_path = "/".join(normalized_path)
    candidates: set[str] = set()
    for sku, patterns in SKU_ALIASES.items():
        if sku.lower() in normalized_path.lower() or any(
               re.search(pattern, normalized_path, re.IGNORECASE) or
               re.search(pattern, spoken, re.IGNORECASE) for pattern in patterns):
            candidates.add(sku)
    if _asr_color_evidence(spoken):
        candidates.add("JXB-COLOR")
    if re.search(r"(?:三色|三款|红蓝白|红色.*蓝色.*白色|白色.*红色.*蓝色)", spoken):
        return None
    if re.search(r"修护", normalized_path) and not candidates:
        candidates.add("JXB-99")
    return next(iter(candidates)) if len(candidates) == 1 else None


def plan_clip_level_fallback(
    hook_sources: Iterable[Mapping[str, Any]],
    body_sources: Iterable[Mapping[str, Any]], *,
    target_product: str = "", target_clips: int = 2,
    strict_failure: str = "", max_variants: int = 256,
    unranked: bool = False,
) -> list[dict[str, Any]]:
    """Build review-only drafts from *whole* source clips after semantic planning fails.

    This never invents a product label or claims that the join is fluent.
    The default ranks product compatibility first, then diagnostic tier. The
    optional unranked mode admits all diagnostic tiers without ordering them,
    but excludes *known* cross-product combinations.  Each file is used at
    most once in an output; variants differ by their Hook/first-Body pair.
    """
    tiers = {"usable": 0, "review": 1, "blocked": 2}

    def prepare(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        prepared = []
        for raw in items:
            source = dict(raw)
            start = max(0.0, float(source.get("selected_in_s") or 0.0))
            end = float(source.get("selected_out_s") or source.get("duration_s") or 0.0)
            if end <= start + 0.05:
                continue
            source["start_s"] = start
            source["end_s"] = end
            source["product_id"] = str(source.get("product_id") or infer_sku_id(
                str(source.get("file") or ""), str(source.get("text") or "")) or "")
            source["status"] = str(source.get("status") or "blocked")
            prepared.append(source)
        return sorted(prepared, key=lambda source: str(source.get("file") or "").lower())

    hooks, bodies = prepare(hook_sources), prepare(body_sources)
    if not hooks or not bodies:
        return []

    def pair_rank(hook: Mapping[str, Any], body: Mapping[str, Any]) -> int:
        left, right = hook["product_id"], body["product_id"]
        if left and right and left != right:
            return 4  # A cross-product cut is a last resort even if transcribed.
        known = left or right
        if known and target_product and known != target_product:
            return 3
        if left and right:
            return 0
        if known:
            return 1
        return 2

    def unranked_compatible(hook: Mapping[str, Any], body: Mapping[str, Any]) -> bool:
        left, right = hook["product_id"], body["product_id"]
        return not ((left and right and left != right) or
                    (target_product and any(product and product != target_product
                                            for product in (left, right))))

    def stable_mix_key(*paths: str) -> int:
        # A stable pseudo-random order keeps preflight and the actual render in
        # sync while avoiding status or product-tier preference in this mode.
        key = "\0".join((target_product, *paths)).encode("utf-8", "surrogatepass")
        return int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big")

    # Do not materialize Hook × Body for a large commercial footage library.
    # Keep a bounded set: ranked by safety normally, or by a stable hash when
    # the user explicitly opts out of the status-tier fallback priority.
    candidate_limit = max(1, min(4096, max(256, int(max_variants) * 4)))
    if unranked:
        pair_candidates = heapq.nsmallest(candidate_limit,
            ((0, stable_mix_key(str(hook["file"]), str(body["file"])), 0, 0,
              str(hook["file"]).lower(), str(body["file"]).lower(), hook, body)
             for hook in hooks for body in bodies
             if hook["file"] != body["file"] and unranked_compatible(hook, body)),
            key=lambda value: value[:6],
        )
    else:
        pair_candidates = heapq.nsmallest(candidate_limit,
            ((pair_rank(hook, body),
              tiers.get(hook["status"], 2) + tiers.get(body["status"], 2),
              tiers.get(hook["status"], 2), tiers.get(body["status"], 2),
              str(hook["file"]).lower(), str(body["file"]).lower(), hook, body)
             for hook in hooks for body in bodies if hook["file"] != body["file"]),
            key=lambda value: value[:6],
        )
    if not pair_candidates:
        return []
    def segment(source: Mapping[str, Any], role: str) -> dict[str, Any]:
        start, end = source["start_s"], source["end_s"]
        product = source["product_id"]
        return {
            "source_id": source["file"], "path": source["file"],
            "file": source["file"], "start": start, "in_s": start,
            "out_s": end, "duration": round(end - start, 3),
            "duration_s": round(end - start, 3),
            "has_audio": bool(source.get("has_audio")),
            "text": str(source.get("selected_text") or source.get("text") or ""),
            "role": role, "sku_id": product, "product_id": product,
            "topic_id": "", "pain_id": "", "mechanism_id": "",
            "fallback_status": source["status"],
            "hook_whole_source": not source.get("selected_out_s") or abs(
                float(source["selected_out_s"]) - float(source.get("duration_s") or end)
            ) < 0.05,
            "source_full_duration_s": source.get("duration_s"),
        }

    plans = []
    seen: set[tuple[str, ...]] = set()
    for rank, _quality, _hook_quality, _body_quality, _hook_path, _body_path, hook, first_body in pair_candidates:
        known = target_product if unranked and target_product else hook["product_id"] or first_body["product_id"]
        compatible = sorted(
            (body for body in bodies if body["file"] != first_body["file"]
             and body["file"] != hook["file"]
             and ((not body["product_id"] or body["product_id"] == known)
                  if unranked else
                  (not known or not body["product_id"] or body["product_id"] == known))),
            key=(lambda body: (stable_mix_key(str(hook["file"]), str(first_body["file"]), str(body["file"])),
                               str(body["file"]).lower())) if unranked else
                (lambda body: (tiers.get(body["status"], 2),
                               0 if body["product_id"] == known else 1,
                               str(body["file"]).lower())),
        )
        chosen_bodies = [first_body] + compatible[:max(0, target_clips - 2)]
        key = tuple([str(hook["file"])] + [str(item["file"]) for item in chosen_bodies])
        if key in seen:
            continue
        seen.add(key)
        selected = [hook] + chosen_bodies
        segments = [segment(hook, "hook")] + [segment(item, "body") for item in chosen_bodies]
        selected_products = {item["product_id"] for item in selected if item["product_id"]}
        product = next(iter(selected_products)) if len(selected_products) == 1 else ""
        warnings = ["语义选段未通过，已降级为完整原片拼接；台词顺接、画面及功效需人工复核"]
        if unranked:
            warnings.append("不做分级保底：同产品候选不按可用、需复核、未采用的状态排序；成片需逐条人工复核")
        if strict_failure:
            warnings.append(f"原语义方案未通过：{strict_failure}")
        if len(selected_products) > 1:
            warnings.append("跨产品，仅供人工复核，不可直接投放")
        elif not product:
            warnings.append("产品无法确认，仅供人工复核，不可直接投放")
        elif target_product and product != target_product:
            warnings.append(f"所选素材产品 {product} 与指定产品 {target_product} 不一致，仅供人工复核，不可直接投放")
        if any(not item["product_id"] for item in selected):
            warnings.append("部分素材产品无法确认，需人工核对画面和口播")
        if any(not item.get("has_audio") for item in selected):
            warnings.append("有素材无有效原声，导出后请检查声音")
        if any(not str(item.get("text") or "").strip() for item in selected):
            warnings.append("有素材无转录/SRT，完整原声已保留，需复听台词")
        if any(item["status"] == "blocked" for item in selected):
            warnings.append("使用了预检未采用素材，详见逐条诊断，必须人工复核")
        if any(TIME_SENSITIVE.search(str(item.get("text") or "")) or
               EARLY_PURCHASE.search(str(item.get("text") or "")) for item in selected):
            warnings.append("含活动/价格/促单话术，时效和真实性未核对，不可直接投放")
        if len(segments) < target_clips:
            warnings.append(f"实际仅 {len(segments)} 段，低于设置 {target_clips} 段；未重复或跨产品补齐")
        plans.append({
            "sku_id": product, "product_id": product,
            "topic_id": "", "pain_id": "", "mechanism_id": "",
            "packaging_version": "", "duration_s": round(sum(item["duration"] for item in segments), 3),
            "hook_duration_s": segments[0]["duration"], "hook_segment_count": 1,
            "transcript": "".join(item["text"] for item in segments),
            "segments": segments, "warnings": list(dict.fromkeys(warnings)),
            "needs_visual_review": True, "needs_speaker_review": True,
            "needs_audio_review": True, "fallback": True,
            "fallback_mode": "unranked_clip_level" if unranked else "clip_level",
            "variant_index": len(plans),
            "rejections": {}, "product_match_rank": rank,
        })
    for plan in plans:
        plan["available_variants"] = len(plans)
    return plans


def infer_topic_ids(text: str) -> set[str]:
    """Find specific spoken themes; usage/packaging alone remain neutral."""
    found = {topic for topic, patterns in TOPIC_ALIASES.items()
             if any(re.search(pattern, text) for pattern in patterns)}
    if _asr_color_evidence(text):
        found.add("whitening")
    return found


def _asr_color_evidence(text: str) -> bool:
    """Recognize a few observed homophones only in an oral-care context."""
    for match in ASR_COLOR_SUSPECT.finditer(text):
        if ASR_COLOR_CONTEXT.search(text[max(0, match.start() - 10):match.end() + 10]):
            return True
    return False


def _fact_ids(text: str, aliases: Mapping[str, tuple[str, ...]]) -> set[str]:
    return {key for key, patterns in aliases.items()
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)}


def _spoken_sku_ids(text: str) -> set[str]:
    return _fact_ids(text, SPOKEN_SKU_PATTERNS)


def _pain_ids(text: str) -> set[str]:
    return _fact_ids(text, PAIN_ALIASES)


def _mechanism_ids(text: str) -> set[str]:
    found = _fact_ids(text, MECHANISM_ALIASES)
    if _asr_color_evidence(text):
        found.add("color_correction")
    return found


def normalize_sku_id(value: Any) -> str | None:
    """Canonicalize a product hint; keep the legacy function name for callers."""
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^俊小白(?:的)?", "", raw)
    upper = raw.upper().replace("_", "-")
    if upper in SKU_ALIASES:
        return upper
    if upper in {"99", "99+", "99牙膏", "99+牙膏", "修护", "修护牙膏", "密集修护"}:
        return "JXB-99"
    aliases = {
        "色修": "JXB-COLOR", "色修美白": "JXB-COLOR",
        "色修牙膏": "JXB-COLOR", "色修美白牙膏": "JXB-COLOR",
        "口干口臭": "JXB-BREATH", "口气": "JXB-BREATH",
        "口干口臭专健牙膏": "JXB-BREATH", "口气牙膏": "JXB-BREATH",
        "釉龈双护": "JXB-ENAMEL-GUM", "釉龈双护牙膏": "JXB-ENAMEL-GUM",
        "换牙期": "JXB-KIDS-MIXED", "换牙期牙膏": "JXB-KIDS-MIXED",
        "青少年": "JXB-KIDS-TEEN", "恒牙期": "JXB-KIDS-TEEN",
        "青少年牙膏": "JXB-KIDS-TEEN", "恒牙期牙膏": "JXB-KIDS-TEEN",
        "乳牙期": "JXB-KIDS-PRIMARY", "乳牙期牙膏": "JXB-KIDS-PRIMARY",
    }
    return aliases.get(raw)


def normalize_topic_id(value: Any) -> str | None:
    """Map common Chinese theme labels to one canonical spoken topic."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lower() in TOPIC_ALIASES:
        return raw.lower()
    topics = infer_topic_ids(raw)
    specific = topics - {"usage"}
    if len(specific) == 1:
        return next(iter(specific))
    if not specific and "usage" in topics:
        return "usage"
    return None


def _seconds(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SemanticSelectionError("bad_time", f"{label} 缺少有效时间码") from exc
    if not math.isfinite(result) or result < 0:
        raise SemanticSelectionError("bad_time", f"{label} 必须是非负有限秒数")
    return result


def _tokens(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = value.split("|")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        raise SemanticSelectionError("bad_annotation", "语义标签必须是字符串或字符串列表")
    return tuple(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _explicit_end(cue: Mapping[str, Any]) -> bool | None:
    if "sentence_end" in cue:
        return bool(cue["sentence_end"])
    if "unit_end" in cue:
        return bool(cue["unit_end"])
    return None


def _complete_phrase(text: str, *, pause: float = 0.0) -> bool:
    text = text.strip()
    if END_PUNCTUATION.search(text):
        return True
    bare = re.sub(r"[，,。！？!?；;、\s]+$", "", text)
    if len(bare) < 4 or bare in {"朋友们", "大家好", "对不对", "三二一"}:
        return False
    if re.search(r"(?:不可逆的|不可递的|(?:都是|变得|闻着).{0,10}(?:香香的|清新的|干干净净的))$", bare):
        return True
    if re.search(r"^(?:先别|不要|不用|直接|朋友们直接).{2,}(?:沾水|干刷|刷牙|挤太多|用起来)$", bare):
        return True
    if re.search(r"(?:你不知道|我赌|我猜).{2,}(?:用法|原因|技巧)$", bare):
        return True
    if DANGLING_END.search(bare):
        return False
    if COMPLETE_INTENT.search(bare):
        return True
    if FINAL_PARTICLE.search(bare) and SENTENCE_VERB.search(bare):
        return True
    if re.search(r"(?:就是|这是).{2,50}(?:牙膏|产品)$", bare):
        return True
    if re.search(r"(?:建议|推荐).{2,50}(?:牙膏|产品|起来|一下)$", bare):
        return True
    if pause >= 0.5 and SENTENCE_VERB.search(bare):
        return True
    return False


def _boundary(current: Mapping[str, Any], following: Mapping[str, Any] | None) -> bool:
    explicit = _explicit_end(current)
    if explicit is not None:
        return explicit
    text = str(current["text"]).strip()
    if END_PUNCTUATION.search(text):
        return True
    if following is None:
        return _complete_phrase(text)
    next_text = str(following["text"]).strip()
    # A connective/pronoun starts a dependent thought; keep it with its lead-in.
    if CONTINUATION.match(next_text):
        return False
    pause = max(0.0, float(following["start"]) - float(current["end"]))
    if pause >= 0.5 and _complete_phrase(text, pause=pause):
        return True
    if FINAL_PARTICLE.search(text) and _complete_phrase(text):
        return True
    if NEW_SENTENCE_START.match(next_text) and _complete_phrase(text, pause=0.5):
        return True
    # An explicitly different annotated role/topic is a topic change only when
    # the preceding speech itself has a complete predicate.
    changed = any(
        current.get(field) and following.get(field)
        and current[field] != following[field]
        for field in ("role", "topic_id")
    )
    return changed and _complete_phrase(text, pause=0.5)


def _infer_role(text: str) -> str:
    if re.search(r"(?:点头像|进直播间|商品卡|下单|去拍|购买)", text) \
            or EARLY_PURCHASE.search(text):
        return "cta"
    if "？" in text or "?" in text or re.search(
        r"^(?:为什么|怎么|你是不是|有没有|知道吗|先别|千万别|我赌你不知道|我读你不知道)", text
    ):
        return "hook"
    if re.search(r"(?:挤到牙刷|挤牙膏|刷牙|干刷|开盖|用量|怎么用|怎么刷|不用.{0,3}太多|就这么一点点)", text):
        return "demonstrate"
    if re.search(r"(?:因为|原理|核心成分|核心就是|主要成分|里面添加|含有|解释一下|封堵牙小管|羟基磷灰石)", text):
        return "explain"
    if re.search(r"(?:就是.{1,40}牙膏|这是.{1,40}牙膏|俊小白.*牙膏|这款牙膏|这支牙膏)", text):
        return "product"
    if re.search(r"(?:担心|困扰|不舒服|口气|口臭|敏感|牙龈)", text):
        return "problem"
    return "unknown"


def _same_annotation(cues: list[dict[str, Any]], field: str, default: Any) -> Any:
    values = [cue[field] for cue in cues if cue.get(field) not in (None, "")]
    unique = list(dict.fromkeys(values))
    if len(unique) > 1:
        raise SemanticSelectionError("conflicting_annotation", f"同一句内的 {field} 标注冲突")
    return unique[0] if unique else default


def _aggregate_offer_status(items: Iterable[Mapping[str, Any]], *,
                            default: str = "none", visual: bool = False) -> str:
    """A verified cue cannot approve a different, unverified promotion cue."""
    entries = list(items)
    field = "visual_offer_status" if visual else "offer_status"
    statuses = [str(item.get(field) or default) for item in entries]
    for status in statuses:
        if status in {"stale", "old", "rejected"}:
            return status
    for status in statuses:
        if status not in {"none", "current"}:
            return status
    if not visual and TIME_SENSITIVE.search("".join(str(item.get("text") or "") for item in entries)):
        return "current" if statuses and all(status == "current" for status in statuses) else "unknown"
    return "current" if "current" in statuses else "none"


def _make_one(
    cues: list[dict[str, Any]], *, source_id: str, path: str, sku_id: str,
    topic_id: str, packaging_version: str, speaker: str, family_id: str,
    sku_evidence: str, default_offer_status: str, default_visual_offer_status: str,
    is_hook_candidate: bool, is_body_candidate: bool,
    analyzer: Callable[[dict[str, Any]], Mapping[str, Any]] | None,
    max_unit_duration_s: float, final_complete: bool,
) -> dict[str, Any]:
    text = "".join(str(cue["text"]).strip() for cue in cues)
    start, end = float(cues[0]["start"]), float(cues[-1]["end"])
    pain_ids, mechanism_ids = _pain_ids(text), _mechanism_ids(text)
    role = _same_annotation(cues, "role", "") or _infer_role(text)
    manual_topic = _same_annotation(cues, "topic_id", "")
    manual_verified = any(cue.get("topic_verified") is True for cue in cues)
    spoken_topics = infer_topic_ids(text)
    specific_topics = spoken_topics - {"usage"}
    if manual_topic and manual_verified:
        resolved_topic, topic_source = manual_topic, "manual_verified"
    elif len(specific_topics) == 1:
        resolved_topic, topic_source = next(iter(specific_topics)), "spoken"
    elif not specific_topics and "usage" in spoken_topics:
        resolved_topic, topic_source = "usage", "spoken"
    else:
        resolved_topic, topic_source = "", "unresolved"
    item: dict[str, Any] = {
        "source_id": source_id,
        "path": path,
        "file": path,
        "in_s": start,
        "start": start,
        "out_s": end,
        "duration_s": round(end - start, 3),
        "duration": round(end - start, 3),
        "has_audio": True,
        "text": text,
        "sku_id": sku_id,
        "sku_evidence": sku_evidence,
        # Keep the legacy sku_id for callers, but plan on product identity.
        # Retail SKU/packaging variants must not become a preflight constraint.
        "product_id": sku_id,
        "product_evidence": sku_evidence,
        "topic_id": resolved_topic,
        "topic_source": topic_source,
        "pain_id": next(iter(pain_ids)) if len(pain_ids) == 1 else "",
        "mechanism_id": next(iter(mechanism_ids)) if len(mechanism_ids) == 1 else "",
        "pain_conflict": len(pain_ids) > 1,
        "mechanism_conflict": len(mechanism_ids) > 1,
        "packaging_version": _same_annotation(cues, "packaging_version", packaging_version),
        "speaker": _same_annotation(cues, "speaker", speaker),
        "family_id": _same_annotation(cues, "family_id", family_id),
        "role": role,
        "introduces": tuple(dict.fromkeys(tag for cue in cues for tag in _tokens(cue.get("introduces")))),
        "requires": tuple(dict.fromkeys(tag for cue in cues for tag in _tokens(cue.get("requires")))),
        "claim_key": _same_annotation(cues, "claim_key", ""),
        "take_group": _same_annotation(cues, "take_group", ""),
        "offer_status": _aggregate_offer_status(cues, default=default_offer_status),
        "visual_offer_status": _aggregate_offer_status(
            cues, default=default_visual_offer_status, visual=True),
        "visible_sku_ids": tuple(dict.fromkeys(tag for cue in cues for tag in _tokens(cue.get("visible_sku_ids")))),
        "visual_tags": tuple(dict.fromkeys(tag for cue in cues for tag in _tokens(cue.get("visual_tags")))),
        "quality_score": _same_annotation(cues, "quality_score", 0),
        "is_hook_candidate": bool(_same_annotation(cues, "is_hook_candidate", is_hook_candidate)),
        "is_body_candidate": bool(_same_annotation(cues, "is_body_candidate", is_body_candidate)),
        "sentence_complete": final_complete and end - start <= max_unit_duration_s,
        "boundary_basis": "explicit" if _explicit_end(cues[-1]) is True or END_PUNCTUATION.search(cues[-1]["text"]) else "inferred",
    }
    if analyzer is not None:
        additions = analyzer(dict(item))
        if not isinstance(additions, Mapping):
            raise SemanticSelectionError("bad_analysis", "语义分析器必须返回字段映射")
        for field, value in additions.items():
            if field not in ANALYZER_FIELDS:
                raise SemanticSelectionError("bad_analysis", f"语义分析器不能覆盖 {field}")
            item[field] = value
        if additions.get("topic_id"):
            item["topic_source"] = "analyzer"
    item["pain_conflict"] = bool(item["pain_conflict"] or (
        item.get("pain_id") and pain_ids and item["pain_id"] not in pain_ids
    ))
    item["mechanism_conflict"] = bool(item["mechanism_conflict"] or (
        item.get("mechanism_id") and mechanism_ids and item["mechanism_id"] not in mechanism_ids
    ))
    inferred_topics = infer_topic_ids(text)
    item["inferred_topics"] = tuple(sorted(inferred_topics))
    specific_inferred = inferred_topics - {"usage"}
    item["topic_conflict"] = bool(
        (len(specific_inferred) > 1 and not manual_verified and item["topic_source"] != "analyzer")
        or (specific_inferred and item["topic_id"] and item["topic_id"] not in specific_inferred)
    )
    introduced = set(_tokens(item["introduces"]))
    required = set(_tokens(item["requires"]))
    unit_sku = str(item["sku_id"] or "")
    unit_topic = str(item["topic_id"] or "")
    if EXPLICIT_PRODUCT.search(text) and unit_sku:
        introduced.add(f"sku:{unit_sku}")
    if item["role"] in {"explain", "demonstrate"} and unit_topic:
        introduced.add(f"premise:{unit_topic}")
    if VOICE_START_PRODUCT.match(text) and not EXPLICIT_PRODUCT.search(text):
        required.add(f"sku:{unit_sku}")
    if VOICE_START_CAUSE.match(text):
        required.add(f"premise:{unit_topic}")
    if item["role"] == "cta":
        required.add(f"sku:{unit_sku}")
    item["introduces"] = tuple(sorted(introduced))
    item["requires"] = tuple(sorted(required))
    item["needs_visual_review"] = item["visual_offer_status"] not in {"none", "current"}
    return item


def make_units(
    cues: Iterable[Mapping[str, Any]], *, source_id: str, path: str = "",
    sku_id: str = "", topic_id: str = "", packaging_version: str = "",
    speaker: str = "", family_id: str = "",
    sku_verified: bool = False,
    default_offer_status: str = "none",
    default_visual_offer_status: str = "unknown",
    is_hook_candidate: bool = False,
    is_body_candidate: bool = False,
    analyzer: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    max_unit_duration_s: float = 15.0,
) -> list[dict[str, Any]]:
    """Merge timed ASR/SRT cues into complete, variable-length speech units.

    Cues require ``start``, ``end``, ``text`` in seconds.  Optional cue labels
    include ``sentence_end``, ``role``, ``topic_id``, ``introduces``, ``requires``,
    ``offer_status`` and ``visual_offer_status``.  Unknown or mixed SKU evidence
    does not inherit a user-selected target label.  Set ``sku_verified`` only
    after a person checks the exact visible product.  Uncertain trailing speech
    is returned with ``sentence_complete=False`` and cannot enter a plan.
    """
    if not source_id:
        raise SemanticSelectionError("missing_source", "口播素材缺少 source_id")
    sku_id = normalize_sku_id(sku_id) or ""
    max_unit_duration_s = _seconds(max_unit_duration_s, "最长句段")
    if max_unit_duration_s <= 0:
        raise SemanticSelectionError("bad_time", "最长句段必须大于零")
    prepared: list[dict[str, Any]] = []
    for raw in cues:
        cue = dict(raw)
        cue["start"] = _seconds(cue.get("start"), "start")
        cue["end"] = _seconds(cue.get("end"), "end")
        cue["text"] = str(cue.get("text") or "").strip()
        if cue["end"] <= cue["start"] or not cue["text"]:
            raise SemanticSelectionError("bad_cue", "转录行必须有非空文本及正时长")
        if prepared and cue["start"] < prepared[-1]["end"] - 0.05:
            raise SemanticSelectionError("overlapping_cues", "转录时间码重叠或未按时间排序")
        prepared.append(cue)
    if not prepared:
        raise SemanticSelectionError("missing_transcript", "没有可用的带时间码口播")

    inferred_sku = infer_sku_id(path, prepared)
    if sku_verified and sku_id:
        resolved_sku, sku_evidence = sku_id, "visual_verified"
    elif inferred_sku and (not sku_id or sku_id == inferred_sku):
        resolved_sku, sku_evidence = inferred_sku, "source_alias"
    else:
        resolved_sku, sku_evidence = "", "unknown_or_mixed"

    result: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for index, cue in enumerate(prepared):
        if pending and cue["start"] - pending[-1]["end"] > 1.5:
            result.append(_make_one(pending, source_id=source_id, path=path, sku_id=resolved_sku,
                                    topic_id=topic_id, packaging_version=packaging_version,
                                    speaker=speaker, family_id=family_id, sku_evidence=sku_evidence,
                                    default_offer_status=default_offer_status,
                                    default_visual_offer_status=default_visual_offer_status,
                                    is_hook_candidate=is_hook_candidate,
                                    is_body_candidate=is_body_candidate,
                                    analyzer=analyzer,
                                    max_unit_duration_s=max_unit_duration_s, final_complete=False))
            pending = []
        pending.append(cue)
        following = prepared[index + 1] if index + 1 < len(prepared) else None
        boundary_probe = dict(cue)
        boundary_probe["text"] = "".join(str(part["text"]).strip() for part in pending)
        if _boundary(boundary_probe, following):
            result.append(_make_one(pending, source_id=source_id, path=path, sku_id=resolved_sku,
                                    topic_id=topic_id, packaging_version=packaging_version,
                                    speaker=speaker, family_id=family_id, sku_evidence=sku_evidence,
                                    default_offer_status=default_offer_status,
                                    default_visual_offer_status=default_visual_offer_status,
                                    is_hook_candidate=is_hook_candidate,
                                    is_body_candidate=is_body_candidate,
                                    analyzer=analyzer,
                                    max_unit_duration_s=max_unit_duration_s, final_complete=True))
            pending = []
    if pending:
        result.append(_make_one(pending, source_id=source_id, path=path, sku_id=resolved_sku,
                                topic_id=topic_id, packaging_version=packaging_version,
                                speaker=speaker, family_id=family_id, sku_evidence=sku_evidence,
                                default_offer_status=default_offer_status,
                                default_visual_offer_status=default_visual_offer_status,
                                is_hook_candidate=is_hook_candidate,
                                is_body_candidate=is_body_candidate,
                                analyzer=analyzer,
                                max_unit_duration_s=max_unit_duration_s, final_complete=False))
    # Neutral sentences may inherit a theme only from adjacent speech in the
    # same source, within a short gap and without a competing explicit theme.
    for index, item in enumerate(result):
        if item["topic_id"] or item["role"] in {"product", "cta"}:
            continue
        neighbours: list[tuple[float, str]] = []
        for step in (-1, 1):
            other_index = index + step
            if not 0 <= other_index < len(result):
                continue
            other = result[other_index]
            gap = (item["in_s"] - other["out_s"] if step < 0
                   else other["in_s"] - item["out_s"])
            if gap <= 1.5 and other["topic_id"] and other["topic_source"] in {
                "spoken", "manual_verified", "analyzer"
            }:
                neighbours.append((abs(gap), other["topic_id"]))
        if neighbours and len({topic for _, topic in neighbours}) == 1:
            item["topic_id"] = neighbours[0][1]
            item["topic_source"] = "adjacent_context"
    return result


def make_full_hook_unit(source_units: Iterable[Mapping[str, Any]], *,
                        full_duration_s: float) -> dict[str, Any]:
    """Keep one uploaded Hook video intact, while checking all of its speech.

    A Hook is an atomic source clip, not the first ASR sentence.  Its silence,
    pauses and later sentences stay in the rendered clip.  A mixed-product or
    unfinished source is not silently shortened to make it fit.
    """
    items = [dict(item) for item in source_units]
    full_duration_s = _seconds(full_duration_s, "Hook 原片时长")
    if not items or full_duration_s <= 0:
        raise SemanticSelectionError("missing_hook", "Hook 缺少有效转录或时长")
    items.sort(key=lambda item: float(item["in_s"]))
    first = items[0]
    text = "".join(str(item["text"]) for item in items)
    product_ids = {str(item.get("product_id") or item.get("sku_id") or "") for item in items}
    topics = {str(item.get("topic_id") or "") for item in items} - {"", "usage"}
    pains = set().union(*(_pain_ids(str(item["text"])) for item in items))
    mechanisms = set().union(*(_mechanism_ids(str(item["text"])) for item in items))
    spoken_skus = _spoken_sku_ids(text)
    # A sidecar may contain only the opening sentence.  Keep the source whole,
    # but do not approve a long unexamined head or tail as a coherent Hook.
    covered_edges = (float(first["in_s"]) <= MAX_HOOK_UNTRANSCRIBED_EDGE_S
                     and full_duration_s - float(items[-1]["out_s"])
                     <= MAX_HOOK_UNTRANSCRIBED_EDGE_S)
    # Judge the ending of the original clip. ASR may have inserted an internal
    # cue boundary or long pause; neither proves the entire source was cut off.
    last_text = str(items[-1]["text"]).strip()
    ending_complete = bool(items[-1].get("sentence_complete")
                           or _complete_phrase(last_text)
                           or (TIME_SENSITIVE.search(text)
                               and ASR_PURCHASE_END.search(last_text)))
    # A purchase prompt inside an opening source would land before its Body.
    purchase_match = EARLY_PURCHASE.search(text)
    if purchase_match is None and TIME_SENSITIVE.search(text):
        purchase_match = ASR_PURCHASE.search(text)
    has_early_cta = bool(purchase_match or any(
        item.get("role") == "cta" or _infer_role(str(item["text"])) == "cta"
        for item in items))
    completeness_reasons = []
    if not ending_complete:
        completeness_reasons.append("Hook 末句未说完或句末无法确认完整")
    if float(items[-1]["out_s"]) > full_duration_s + 0.02:
        completeness_reasons.append("转录时间超出 Hook 视频时长")
    if not covered_edges:
        completeness_reasons.append("Hook 开头或结尾超过 3 秒没有转录，无法确认原片完整")
    if has_early_cta:
        if purchase_match:
            completeness_reasons.append(
                f"Hook 含促单引导「{purchase_match.group(0)}」，放在 Body 前会打断叙事")
        else:
            completeness_reasons.append("Hook 含下单引导，放在 Body 前会打断叙事")
    if _RETAKE.search(text):
        completeness_reasons.append("原片含重拍口令")
    if (len(product_ids) != 1 or "" in product_ids or len(spoken_skus) > 1
            or (spoken_skus and spoken_skus != product_ids)):
        completeness_reasons.append("Hook 产品未识别或混入不同产品")
    if re.match(r"^(?:因为|所以|因此|但是|不过|然而|然后|接下来)",
                str(items[0]["text"])) or _PRONOUN_START.match(str(items[0]["text"])) \
            or _ADDITIVE_START.match(str(items[0]["text"])):
        completeness_reasons.append("Hook 开头依赖前文，不能独立作开场")
    complete = not completeness_reasons
    result = dict(first)
    result.update({
        "in_s": 0.0, "start": 0.0, "out_s": full_duration_s,
        "duration_s": round(full_duration_s, 3), "duration": round(full_duration_s, 3),
        "text": text, "role": "hook", "sentence_complete": complete,
        "product_id": next(iter(product_ids)) if len(product_ids) == 1 else "",
        "topic_id": next(iter(topics)) if len(topics) == 1 else "",
        "topic_conflict": len(topics) > 1 or any(item.get("topic_conflict") for item in items),
        "pain_id": next(iter(pains)) if len(pains) == 1 else "",
        "pain_conflict": len(pains) > 1 or any(item.get("pain_conflict") for item in items),
        "mechanism_id": next(iter(mechanisms)) if len(mechanisms) == 1 else "",
        "mechanism_conflict": len(mechanisms) > 1 or any(item.get("mechanism_conflict") for item in items),
        "is_hook_candidate": True, "is_body_candidate": False,
        "hook_whole_source": True,
        "source_sentence_count": len(items),
        "completeness_reasons": tuple(completeness_reasons),
        "introduces": tuple(sorted(set().union(*(set(_tokens(item.get("introduces"))) for item in items)))),
        "requires": tuple(_tokens(first.get("requires"))),
        "visible_sku_ids": tuple(sorted(set().union(*(set(_tokens(item.get("visible_sku_ids"))) for item in items)))),
        "boundary_basis": "inferred" if any(item.get("boundary_basis") == "inferred" for item in items) else "explicit",
    })
    result["offer_status"] = _aggregate_offer_status(items)
    result["visual_offer_status"] = _aggregate_offer_status(items, default="unknown", visual=True)
    result["needs_visual_review"] = result["visual_offer_status"] not in {"none", "current"}
    return result


def complete_hook_opening_before_promo(
    cues: Iterable[Mapping[str, Any]], *, full_duration_s: float,
) -> tuple[list[dict[str, Any]], float] | None:
    """Select a whole opening thought before the first time-sensitive offer.

    This is a conservative fallback for an uploaded Hook that also contains a
    later sales pitch.  It never cuts a cue or invents a sentence boundary: a
    cue must itself end as a complete phrase, and the chosen prefix must be at
    least two seconds long.  A price question without a complete pre-offer
    answer does not qualify.  The caller must still validate the selected
    source range with :func:`make_full_hook_unit` and product checks.
    """
    ordered = [dict(cue) for cue in cues]
    if not ordered:
        return None
    full_duration_s = _seconds(full_duration_s, "Hook 原片时长")
    first_promo = next((index for index, cue in enumerate(ordered) if
                        TIME_SENSITIVE.search(str(cue.get("text") or ""))
                        or EARLY_PURCHASE.search(str(cue.get("text") or ""))
                        or ASR_PURCHASE.search(str(cue.get("text") or ""))
                        or str(cue.get("role") or "") == "cta"), None)
    if first_promo is None or first_promo == 0:
        return None
    for index in range(first_promo - 1, -1, -1):
        cue = ordered[index]
        end_s = _seconds(cue.get("end"), "Hook 句末时间")
        text = str(cue.get("text") or "").strip()
        if (end_s - float(ordered[0].get("start") or 0) < 2.0
                or end_s > full_duration_s + 0.02
                or not _complete_phrase(text)
                or _RETAKE.search(text)):
            continue
        selected = ordered[:index + 1]
        selected_text = "".join(str(part.get("text") or "") for part in selected)
        if TIME_SENSITIVE.search(selected_text) or EARLY_PURCHASE.search(selected_text) \
                or ASR_PURCHASE.search(selected_text):
            continue
        return selected, end_s
    return None


def unit_rejection_reason(unit: Mapping[str, Any], *, sku_id: str, topic_id: str,
                          packaging_version: str) -> str | None:
    """Gate on confirmed product and edit safety, not theme or retail SKU.

    ``sku_id`` is the legacy argument name for the canonical product ID.
    ``topic_id`` and ``packaging_version`` remain for compatibility only.
    """
    if not unit.get("sentence_complete"):
        return "句子边界不完整或句段过长"
    if unit.get("role") not in ROLES:
        return "叙事角色未知"
    product_id = str(unit.get("product_id") or unit.get("sku_id") or "")
    if not product_id or product_id != sku_id:
        return "产品无法确认或不一致"
    spoken_skus = _spoken_sku_ids(str(unit.get("text") or ""))
    if len(spoken_skus) > 1 or (spoken_skus and spoken_skus != {sku_id}):
        return "口播提及其他产品或多款产品"
    if _RETAKE.search(str(unit.get("text") or "")):
        return "原片含重拍口令"
    visible = set(_tokens(unit.get("visible_sku_ids")))
    visible_products = {normalize_sku_id(value) or value for value in visible}
    if visible_products and visible_products != {sku_id}:
        return "画面出现其他产品或多款混用"
    if unit.get("visual_offer_status") in {"stale", "old", "rejected"}:
        return "画面含明确旧价促"
    if unit.get("offer_status") not in {"none", "current"}:
        return "口播活动或价格未通过当期核对"
    if TIME_SENSITIVE.search(str(unit.get("text", ""))) and unit.get("offer_status") != "current":
        return "时效性口播没有当期核对"
    if unit.get("has_audio") is False:
        return "无口播画面不能承担原声句段"
    return None


def _file_key(path: Any) -> str:
    return os.path.normcase(os.path.normpath(str(path or "")))


def _segment_key(unit: Mapping[str, Any]) -> tuple[str, float, float]:
    return (str(unit.get("source_id") or ""), float(unit["in_s"]), float(unit["out_s"]))


def _quality_score(unit: Mapping[str, Any]) -> float:
    try:
        value = float(unit.get("quality_score") or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _known_speaker(unit: Mapping[str, Any]) -> str:
    value = str(unit.get("speaker") or "").strip()
    return "" if value.lower() in {"", "unknown", "待核", "未知", "未确认"} else value


def _names_product(unit: Mapping[str, Any]) -> bool:
    text = str(unit.get("text") or "")
    return bool(re.search(
        r"(?:(?:俊|郡|军|菌)小白.{0,12}(?:牙膏|牙高|雅高|修护)|"
        r"(?:色修|口干口臭|釉龈双护).{0,8}(?:牙膏|牙高|雅高)|"
        r"nHAP[-_ ]?Pro|mHAP[-_ ]?White)", text, re.IGNORECASE,
    ))


_COUNT_WORDS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _offer_claims(text: str) -> dict[str, int | float]:
    """Compare only explicit, singular counts/prices across joined clips."""
    patterns = {
        "purchase_count": r"(?:拍\s*一\s*发|下单|买)\s*([\d一二两三四五六七八九十]+)\s*(?:支|件|盒|瓶|套)?",
        "gift_count": r"(?:送|赠)\s*(?:你)?\s*([\d一二两三四五六七八九十]+)\s*(?:支|件|盒|份|个|包)?",
        "price_yuan": r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:元|块)",
    }
    claims: dict[str, int | float] = {}
    for key, pattern in patterns.items():
        values = set()
        for match in re.finditer(pattern, text):
            raw = match.group(1)
            if raw.isdigit():
                values.add(int(raw))
            elif key == "price_yuan":
                values.add(float(raw))
            elif raw in _COUNT_WORDS:
                values.add(_COUNT_WORDS[raw])
        if len(values) == 1:
            claims[key] = next(iter(values))
    return claims


def _fact_conflicts(path: tuple[dict[str, Any], ...], candidate: Mapping[str, Any]) -> bool:
    # Pain points and mechanisms may differ within an otherwise coherent
    # product story.  Only contradictory concrete offer numbers are factual
    # conflicts across clips; discourse checks below handle fluent joins.
    candidate_claims = _offer_claims(str(candidate.get("text") or ""))
    for prior in path:
        prior_claims = _offer_claims(str(prior.get("text") or ""))
        if any(prior_claims.get(key) != value for key, value in candidate_claims.items()
               if key in prior_claims):
            return True
    return False


def _transition_coherent(path: tuple[dict[str, Any], ...], candidate: Mapping[str, Any]) -> bool:
    """Reject obvious broken discourse, especially at a cross-source join."""
    previous = path[-1]
    text = str(candidate.get("text") or "").strip()
    previous_text = str(previous.get("text") or "").strip()
    same_source_continuation = (
        candidate.get("source_id") == previous.get("source_id")
        and 0 <= float(candidate["in_s"]) - float(previous["out_s"]) <= 1.5
    )
    if candidate.get("role") == "hook":
        # Every added Hook must be a self-contained clip, not an answer whose
        # missing antecedent happened to be in another source.
        return not (_PRONOUN_START.match(text) or _ADDITIVE_START.match(text)
                    or _SEQUENCE_START.match(text) or _CONTRAST_START.match(text))
    if _CONTRAST_START.match(text) and not same_source_continuation:
        return False
    if _ADDITIVE_START.match(text) and previous.get("role") == "hook":
        return False
    if _SEQUENCE_START.match(text) and previous.get("role") not in {"demonstrate", "explain"}:
        return False
    if previous.get("role") == "product" and candidate.get("role") in MIDDLE_ROLES \
            and not same_source_continuation:
        if not (re.match(r"^(?:它|这款|这支|这个|这种|核心|里面|其中|之所以)", text)
                or _mechanism_ids(text)):
            return False
    if "这个成分" in text and not any(
        _mechanism_ids(str(item.get("text") or ""))
        or re.search(r"(?:羟基磷灰石|蛋膜肽|DCPD|活性成分)", str(item.get("text") or ""))
        for item in path[-2:]
    ):
        return False
    if re.match(r"^(?:这个|这种)成分", text):
        if not any(re.search(r"(?:成分|羟基磷灰石|磷灰石|DCPD|蛋膜肽|色修)",
                             str(item.get("text") or "")) for item in path[-2:]):
            return False
    elif _PRONOUN_START.match(text):
        if not any(re.search(r"(?:俊小白|牙膏|这款|这支|产品|nHAP)",
                             str(item.get("text") or ""), re.IGNORECASE) for item in path[-2:]):
            return False
    if VOICE_START_CAUSE.match(text) and not any(
        item.get("role") in {"problem", "explain", "demonstrate"}
        or re.search(r"(?:为什么|原因|因为|原理|怎么)", str(item.get("text") or ""))
        for item in path[-2:]
    ):
        return False
    if previous_text.endswith(("？", "?")) and _ADDITIVE_START.match(text):
        return False
    return True


def _can_append(path: tuple[dict[str, Any], ...], candidate: dict[str, Any]) -> bool:
    stage = ROLE_STAGE[candidate["role"]]
    if stage < ROLE_STAGE[path[-1]["role"]]:
        return False
    if candidate["role"] == "hook" and path[-1]["role"] != "hook":
        return False
    if _fact_conflicts(path, candidate) or not _transition_coherent(path, candidate):
        return False
    if candidate["role"] == "cta" and not any(
        x["role"] == "product" or _names_product(x) for x in path
    ):
        return False
    if candidate["role"] in {"product", "cta"} and any(x["role"] == candidate["role"] for x in path):
        return False
    if sum(x["role"] in MIDDLE_ROLES for x in path) >= 3 and candidate["role"] in MIDDLE_ROLES:
        return False
    introduced = set().union(*(set(_tokens(x.get("introduces"))) for x in path))
    if not set(_tokens(candidate.get("requires"))).issubset(introduced):
        return False
    prior_speakers = {_known_speaker(unit) for unit in path} - {""}
    speaker = _known_speaker(candidate)
    if speaker and prior_speakers and speaker not in prior_speakers:
        return False
    candidate_key = (candidate.get("source_id"), candidate.get("in_s"), candidate.get("out_s"))
    if any((x.get("source_id"), x.get("in_s"), x.get("out_s")) == candidate_key for x in path):
        return False
    for prior in path:
        # Preserve the original chronology of every reused source, including
        # nonoverlapping ranges which would otherwise jump backwards.
        if candidate.get("source_id") == prior.get("source_id") and candidate["in_s"] < prior["out_s"]:
            return False
        for field in ("claim_key", "take_group"):
            if candidate.get(field) and candidate[field] == prior.get(field):
                return False
        if re.sub(r"\W+", "", candidate["text"]) == re.sub(r"\W+", "", prior["text"]):
            return False
        # Repeated takes can differ by a few filler words.  Four-character
        # shingles catch that without treating every shared product name as a
        # duplicate claim.
        left = re.sub(r"\W+", "", str(candidate["text"]))
        right = re.sub(r"\W+", "", str(prior["text"]))
        if min(len(left), len(right)) >= 8:
            shingles_left = {left[i:i + 4] for i in range(len(left) - 3)}
            shingles_right = {right[i:i + 4] for i in range(len(right) - 3)}
            if len(shingles_left & shingles_right) / min(len(shingles_left), len(shingles_right)) >= 0.8:
                return False
    return True


def _finished(path: tuple[dict[str, Any], ...], require_cta: bool) -> bool:
    return (any(x["role"] in MIDDLE_ROLES for x in path)
            and any(x["role"] == "product" or _names_product(x) for x in path)
            and (not require_cta or path[-1]["role"] == "cta"))


def plan_semantic_mix(
    units: Iterable[Mapping[str, Any]], *, sku_id: str, topic_id: str,
    packaging_version: str = "", target_duration_s: float | None = None,
    min_duration_s: float = 0.0, max_duration_s: float = 60.0,
    max_segments: int = 6, target_clips: int | None = None,
    require_cta: bool = False, variant_index: int = 0,
    exclude_keys: Iterable[tuple[str, float, float]] = (),
    hook_files: Iterable[str] | None = None,
    body_files: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Choose an intact Hook block -> coherent Body -> optional CTA path.

    Every returned segment is an original source range with its original text.
    The legacy ``sku_id`` argument identifies the product family, not a retail
    packaging SKU; topic metadata is no longer used as a preflight gate.
    A failed plan raises ``SemanticSelectionError`` with ``code`` and ``details``;
    there is no random-order fallback.
    """
    sku_id = normalize_sku_id(sku_id) or ""
    topic_id = normalize_topic_id(topic_id) or ""
    if not sku_id:
        raise SemanticSelectionError("missing_target", "规划需要明确的产品")
    max_duration_s = _seconds(max_duration_s, "最长成片")
    min_duration_s = _seconds(min_duration_s, "最短成片")
    if max_duration_s <= 0 or min_duration_s > max_duration_s or max_segments < 2:
        raise SemanticSelectionError("bad_limits", "成片时长或片段数量限制无效")
    if target_clips is not None and not 2 <= target_clips <= max_segments:
        raise SemanticSelectionError("bad_limits", "target_clips 必须在 2 到 max_segments 之间")
    if variant_index < 0:
        raise SemanticSelectionError("bad_limits", "variant_index 不能为负数")
    if target_duration_s is not None:
        target_duration_s = _seconds(target_duration_s, "目标时长")
        if not min_duration_s <= target_duration_s <= max_duration_s:
            raise SemanticSelectionError("bad_limits", "目标时长不在成片范围内")

    rejected: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    excluded = {(str(key[0]), float(key[1]), float(key[2])) for key in exclude_keys}
    hook_file_keys = {_file_key(path) for path in hook_files} if hook_files is not None else None
    body_file_keys = {_file_key(path) for path in body_files} if body_files is not None else None
    for raw in units:
        unit = dict(raw)
        reason = unit_rejection_reason(unit, sku_id=sku_id, topic_id=topic_id,
                                       packaging_version=packaging_version)
        if reason:
            rejected[reason] += 1
        elif _segment_key(unit) in excluded:
            rejected["片段已用于其他变体"] += 1
        else:
            candidates.append(unit)
    candidates.sort(key=lambda x: (-_quality_score(x),
                                   str(x.get("source_id") or ""), float(x["in_s"])))
    hooks = [x for x in candidates if x["role"] == "hook" and (
        _file_key(x.get("path")) in hook_file_keys if hook_file_keys is not None
        else x.get("is_hook_candidate") is True
    )][:32]
    bodies = [x for x in candidates if x["role"] != "hook" and (
        _file_key(x.get("path")) in body_file_keys if body_file_keys is not None
        else x.get("is_body_candidate") is True
    )]
    if not hooks:
        raise SemanticSelectionError("missing_hook", "缺少完整、同产品的开场口播",
                                     {"rejections": dict(rejected)})
    if not any(x["role"] in MIDDLE_ROLES for x in bodies):
        raise SemanticSelectionError("missing_middle", "缺少问题、演示或解释句段",
                                     {"rejections": dict(rejected)})
    if not any(x["role"] == "product" for x in bodies) and not any(_names_product(x) for x in hooks):
        raise SemanticSelectionError("missing_product", "缺少明确产品承接句段",
                                     {"rejections": dict(rejected)})
    if require_cta and not any(x["role"] == "cta" for x in bodies):
        raise SemanticSelectionError("missing_cta", "要求成交引导，但没有合格原声句段",
                                     {"rejections": dict(rejected)})

    beam: list[tuple[dict[str, Any], ...]] = [(hook,) for hook in hooks]
    complete: list[tuple[dict[str, Any], ...]] = []
    for _ in range(1, max_segments):
        next_beam: list[tuple[dict[str, Any], ...]] = []
        for path in beam:
            options = ([hook for hook in hooks if hook is not path[-1]] + bodies
                       if path[-1]["role"] == "hook" else bodies)
            for candidate in options:
                if not _can_append(path, candidate):
                    continue
                duration = sum(float(x["duration_s"]) for x in path) + float(candidate["duration_s"])
                if duration > max_duration_s:
                    continue
                expanded = (*path, candidate)
                if (_finished(expanded, require_cta) and duration >= min_duration_s
                        and (target_clips is None or len(expanded) == target_clips)):
                    complete.append(expanded)
                if (candidate["role"] != "cta" and len(expanded) < max_segments
                        and (target_clips is None or len(expanded) < target_clips)):
                    next_beam.append(expanded)
        if not next_beam:
            break
        # Keep a bounded search while favouring progress through the narrative.
        next_beam.sort(key=lambda path: (
            -ROLE_STAGE[path[-1]["role"]],
            abs(sum(x["duration_s"] for x in path) - target_duration_s)
            if target_duration_s is not None else len(path),
            -sum(_quality_score(x) for x in path),
            tuple((str(x["source_id"]), x["in_s"]) for x in path),
        ))
        # Reserve space at each stage so a long, fully coherent middle is not
        # displaced by short paths which reached the product too early.
        beam = []
        for stage in (0, 1, 2, 3):
            stage_paths = [path for path in next_beam if ROLE_STAGE[path[-1]["role"]] == stage]
            beam.extend(stage_paths[:64])
    if not complete:
        raise SemanticSelectionError(
            "no_coherent_path", "没有可按原声逻辑连续拼接的句段；需补口播标注或素材",
            {"rejections": dict(rejected), "eligible_count": len(candidates)},
        )

    def rank(path: tuple[dict[str, Any], ...]) -> tuple[Any, ...]:
        duration = sum(float(x["duration_s"]) for x in path)
        # Relevance is a preference, not a preflight consistency gate.  A
        # complete Body that directly answers the Hook's stated concern should
        # rank ahead of a same-product but unrelated usage tip, even if its
        # duration is a little farther from the target.
        hook_pains = set().union(*(
            _pain_ids(str(item.get("text") or "")) for item in path
            if item["role"] == "hook"
        ))
        body_pains = set().union(*(
            _pain_ids(str(item.get("text") or "")) for item in path
            if item["role"] != "hook"
        ))
        return (
            -len(hook_pains & body_pains),
            abs(duration - target_duration_s) if target_duration_s is not None else len(path),
            -len({x["role"] for x in path}),
            -sum(_quality_score(x) for x in path),
            duration,
            tuple((str(x["source_id"]), x["in_s"]) for x in path),
        )

    ranked = sorted(complete, key=rank)
    unique: list[tuple[dict[str, Any], ...]] = []
    seen_paths: set[tuple[tuple[str, float, float], ...]] = set()
    for path in ranked:
        signature = tuple(_segment_key(unit) for unit in path)
        if signature not in seen_paths:
            unique.append(path)
            seen_paths.add(signature)
    # Select variants as a set, not merely the first N near-identical ranked
    # paths.  Keep the best edit first, then prefer unused source videos where
    # possible.  A single-source pool still works; quality ranking breaks ties.
    available_variants = len(unique)
    if variant_index >= available_variants:
        raise SemanticSelectionError(
            "insufficient_variants", "满足逻辑和时长限制的不同素材组合不足",
            {"available_variants": available_variants, "requested_variant_index": variant_index},
        )
    diverse: list[tuple[dict[str, Any], ...]] = []
    remaining = list(unique)
    source_usage: Counter[str] = Counter()
    for _ in range(variant_index + 1):
        best = min(remaining, key=lambda path: (
            sum(source_usage[str(item["source_id"])]
                for item in path),
            rank(path),
        ))
        diverse.append(best)
        source_usage.update(str(item["source_id"]) for item in best)
        remaining.remove(best)
    winner = diverse[variant_index]
    segments = [dict(item) for item in winner]
    hook_segments = [item for item in segments if item["role"] == "hook"]
    pain_ids = ({str(item.get("pain_id") or "") for item in segments} - {""}) | set().union(
        *(_pain_ids(str(item.get("text") or "")) for item in segments)
    )
    mechanism_ids = ({str(item.get("mechanism_id") or "") for item in segments} - {""}) | set().union(
        *(_mechanism_ids(str(item.get("text") or "")) for item in segments)
    )
    warnings = []
    if any(item.get("visual_offer_status") not in {"none", "current"} for item in segments):
        warnings.append("画面旧价促未检查：仅可作剪辑预览，导出投放前需人工复核")
    if any(not _known_speaker(item) for item in segments):
        warnings.append("部分口播说话人未核对；姓名不能仅凭画面或同名字幕推定")
    if any(item.get("boundary_basis") == "inferred" for item in segments):
        warnings.append("部分句段边界由无标点 ASR 推断；请复听切点是否截字")
    return {
        "sku_id": sku_id,
        "product_id": sku_id,
        "topic_id": topic_id,
        "pain_id": next(iter(pain_ids)) if pain_ids else "",
        "mechanism_id": next(iter(mechanism_ids)) if mechanism_ids else "",
        "packaging_version": packaging_version,
        "duration_s": round(sum(x["duration_s"] for x in segments), 3),
        "hook_duration_s": round(sum(x["duration_s"] for x in hook_segments), 3),
        "hook_segment_count": len(hook_segments),
        "transcript": "".join(x["text"] for x in segments),
        "segments": segments,
        "warnings": warnings,
        "needs_visual_review": any(item.get("visual_offer_status") not in {"none", "current"}
                                   for item in segments),
        "needs_speaker_review": any(not _known_speaker(item) for item in segments),
        "needs_audio_review": any(item.get("boundary_basis") == "inferred" for item in segments),
        "variant_index": variant_index,
        "available_variants": available_variants,
        "rejections": dict(rejected),
    }
