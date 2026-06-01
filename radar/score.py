"""打分：调 Anthropic Messages API（Haiku），给每条内容打信号分 + 标签 + 一句话理由。

- 模型 claude-haiku-4-5-20251001。
- 本批新内容一次性喂入（分批，每批 SCORE_BATCH_SIZE 条），返回严格 JSON 数组：
  [{"id","signal":"high|medium|low","tags":[...],"why":"≤20字"}]。
- 系统提示固定、精简，并打 prompt cache，便于命中缓存省钱。
- 判定标准/标签集见规格「信号分与标签」那节，已固化进 SYSTEM_PROMPT。

健壮性：模型漏返回某 id → 默认 low；signal 非法 → low；tags 只保留合法集合内的；
why 截断到 20 字。解析失败时把该批整体标 low（不丢条目、不中断流水线）。
"""
from __future__ import annotations

import json
import logging
import re

from radar.config import (
    SCORE_BATCH_SIZE,
    SCORE_MAX_TOKENS,
    SCORE_MODEL,
    SOURCE_AUTHORS,
    TAGS,
    env,
    source_type,
)

log = logging.getLogger("radar.score")

_TAGSET = set(TAGS)
_VALID_SIGNAL = {"high", "medium", "low"}

# 固定系统提示（保持精简、不随批次变化 → prompt cache 命中）
SYSTEM_PROMPT = """你是一个中美政策情报分析助手，服务一位给企业做中美经贸/政策咨询的顾问。
对下面每条内容打分并结构化。**只能基于该条已有信息，绝不添加原文没有的事实、不杜撰数字。**

输出严格的 JSON 数组，每条对应一个对象，不要任何额外文字、不要 markdown：
[{"id":"...","signal":"high|medium|low","rumor":true|false,"tags":[...],"headline":"...","summary":"...","impact":"...","author":"...","involved":[...]}]

字段规则：
- headline：用中文重写一个干净、准确、突出重点的标题，≤28字，中性不夸张，先给关键事实。例：法律文件名「Implementing Certain Tariff-Related Elements…AIT…」→「特朗普签 EO 14346：台美贸易协议关税条款正式落地」。
- summary：2–3 句，讲清「发生了什么 / 核心论点是什么」，具体不空泛。low 给 1 短句即可。
- impact：单独一句，回答 so what——影响谁、接下来看什么；面向给企业做中美咨询的读者，要具体。是基于该信号的合理推断，但不杜撰具体事实。例 ODI 条例→「中资出海审批趋严，赴美及全球并购需重估合规路径与时间表」。low 可留空字符串。
- author（观点来自，本条作者本人）：待评内容给了 "author" 字段就用它；官方源填发布机构(如 USTR、商务部 BIS)；新闻源填署名记者，抽不到填媒体名。
- involved（涉及，被讲的人/机构/国家对象）：与 author 分开，可空 []。

tags 只能从这个集合里选(可多选)：["关税","出口管制","制裁金融","华盛顿政治","高层互动","新能源","AI","医药"]
- 关税 = 关税税率、Section 301/232、贸易调查、贸易协定、反倾销。【Section 301/232 一律归"关税"，不归"出口管制"】
- 出口管制 = 出口管制、实体清单、BIS/EAR 规则、对外投资审查(outbound investment)
- 制裁金融 = OFAC 制裁、SDN 名单、金融管制、CFIUS
- 只要涉及中美经贸/科技/金融/政策至少打 1 个标签；确实无关才空 []。
- BIS/EAR 出口管制执法令(对个人或企业的处罚令、列入实体清单、"In the Matter of"类)即使判 low，也必须打"出口管制"标签。
- digest 型条目(一条含多话题，如 Sinocism)：按最具政策相关性的子话题判 signal 与标签，不要只看第一个话题。

signal = 重要性 × 可信度 双维度。可信度【按措辞判定，不要仅按来源】。待评内容已给每条 "source_type"（官方/观点/媒体）仅作参考：
- 已确认的官方行动（announced/launched/initiated/issued/published/finalized/imposed、已签署/正式启动/已发布/已落地/已生效 等【已然措辞】）：即使经 Google News 代理的官方源(USTR/OFAC 等 source_type=媒体)，也按高可信处理，可评 high、rumor=false。
- 未定调措辞（possible/may/signals/weighing/considering/或将/考虑/拟/传/有望 等）且来自单一二手媒体(media) → signal 最高只能 medium，且 rumor=true。
- 知名分析师本人(观点源作者)的深度判断=高可信，但属分析非事件，重要性通常 medium。
- 重要性：high=官方规则/制裁/关税/名单已落地、正式启动的贸易调查或制裁程序(如 Section 301/232 立案，即便针对越南等第三国、只要实质涉华转口/供应链也算 high)、记者成稿前独家风声("sources tell me")、高层会晤确认、关键人物首次明确表态；medium=智库/分析师深度分析、政策预判、数据更新、第三国话题仅泛泛牵动而非具体官方行动；low=转述、寒暄、旧闻、纯美国或纯中国国内且与中美无关。

rumor：默认 false；仅「未定调措辞 + 单一二手媒体」这类未证实风声为 true。已确认官方行动一律 false。

每条都必须在结果里出现一次，id 原样回填(批内序号)。所有字符串内部不要使用未转义的英文双引号，必要时用中文「」。"""


# 「今日主线」综述用的系统提示
DAILY_SYSTEM = """你是中美政策情报分析师。下面是今天捕捉到的高/中信号条目(headline + 摘要)。
请写一段不超过 120 字的「今日主线」：严格基于这些条目，把散落的高信号归纳成 1 个核心判断(趋势或关联)，
面向"明天要给客户开会"的顾问。不要引入这些条目之外的事实、不夸大因果、不堆砌罗列、不用 markdown。
直接输出这一段话，不要标题、不要前后缀。若条目太少不足以归纳，就用一句话点出当下最值得盯的那条。"""


def score_items(items: list[dict], client=None) -> list[dict]:
    """给 items 就地填充 signal/tags/why，返回同一列表。

    无新条目直接返回；ANTHROPIC_API_KEY 缺失则抛错（打分是流水线必需步骤）。
    """
    if not items:
        return items
    if client is None:
        client = _make_client()

    for start in range(0, len(items), SCORE_BATCH_SIZE):
        batch = items[start : start + SCORE_BATCH_SIZE]
        try:
            text = _call(client, batch)
            results = _parse(text)
        except Exception as e:  # noqa: BLE001 — 单批失败不连累其他批/其他流水线步骤
            log.error("打分批次 [%d:%d] 失败，整批标 low：%s", start, start + len(batch), e)
            results = []
        _apply(batch, results)
    return items


# ── API 调用 ──────────────────────────────────────────────────
def _make_client():
    from anthropic import Anthropic

    key = env("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("缺少 ANTHROPIC_API_KEY，无法打分")
    return Anthropic(api_key=key)


def summarize_daily(items: list[dict], client=None) -> str:
    """把当天 high+medium 的 headline/摘要喂给模型，生成 ≤120 字「今日主线」。

    无 high/medium 时返回空串（看板不展示主线）。
    """
    hm = [it for it in items if it.get("signal") in ("high", "medium")]
    if not hm:
        return ""
    if client is None:
        client = _make_client()
    lines = []
    for it in hm:
        head = it.get("headline") or it.get("title") or ""
        syn = (it.get("synopsis") or "").strip()
        lines.append(f"- [{it['signal']}] {head}｜{syn}")
    content = "今日 high/medium 条目：\n" + "\n".join(lines)
    try:
        msg = client.messages.create(
            model=SCORE_MODEL,
            max_tokens=400,
            system=[{"type": "text", "text": DAILY_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    except Exception as e:  # noqa: BLE001 — 主线生成失败不应阻断 render
        log.error("今日主线生成失败：%s", e)
        return ""


def _call(client, batch: list[dict]) -> str:
    msg = client.messages.create(
        model=SCORE_MODEL,
        max_tokens=SCORE_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": _payload(batch)}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _payload(batch: list[dict]) -> str:
    """把本批条目压成紧凑 JSON 交给模型（只给打分需要的字段）。

    用批内序号(0,1,2…)当 id 发给模型，而非真实 item id——真实 id 多为超长
    Google News/URL 串，模型回填时会篡改/截断导致匹配不上。短序号不会被改。
    """
    compact = []
    for idx, it in enumerate(batch):
        src = it.get("source", "")
        _, stype_label = source_type(src)
        rec = {
            "id": str(idx),
            "source": src,
            "source_type": stype_label,  # 官方/观点/媒体 → 供可信度判定
            "title": it.get("title", ""),
            "excerpt": (it.get("summary") or "")[:400],
        }
        author = SOURCE_AUTHORS.get(src)
        if author:
            rec["author"] = author  # 人物源默认作者
        compact.append(rec)
    return "待评内容：\n" + json.dumps(compact, ensure_ascii=False)


# ── 解析 + 回填 ───────────────────────────────────────────────
def _parse(text: str) -> list[dict]:
    """从模型回复里抠出 JSON 数组。容忍 markdown 围栏/前后杂字。

    若整体 json.loads 失败（最常见：模型在某条 why 里写了未转义的英文引号，
    破坏该对象），退化到逐对象 salvage：能解析的照收，只丢坏掉的那一条。
    """
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        i, j = text.find("["), text.rfind("]")
        raw = text[i : j + 1] if (i != -1 and j != -1 and j > i) else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        salvaged = _salvage(raw)
        log.warning("整体 JSON 解析失败，salvage 抢救出 %d 条对象", len(salvaged))
        return salvaged


def _salvage(raw: str) -> list[dict]:
    """逐个 {...} 解析，跳过任何无法解析的对象，最大化回收。"""
    dec = json.JSONDecoder()
    out: list[dict] = []
    pos = raw.find("[")
    pos = 0 if pos == -1 else pos + 1
    n = len(raw)
    while pos < n:
        nb = raw.find("{", pos)
        if nb == -1:
            break
        try:
            obj, end = dec.raw_decode(raw, nb)
            if isinstance(obj, dict):
                out.append(obj)
            pos = end
        except Exception:
            pos = nb + 1  # 这条坏了，跳到下一个 { 继续
    return out


def _apply(batch: list[dict], results: list[dict]) -> None:
    # 模型回填的是批内序号(见 _payload)，按序号映射回真实条目
    by_idx = {}
    for r in results:
        if isinstance(r, dict) and r.get("id") is not None:
            by_idx[str(r["id"])] = r

    for idx, it in enumerate(batch):
        r = by_idx.get(str(idx))
        if not r:
            # 模型漏评 → 保守标 low，进库但折叠
            it["signal"], it["tags"] = "low", []
            it["headline"] = it.get("title", "")
            it["synopsis"], it["impact"], it["author"] = "", "", ""
            it["involved"], it["rumor"] = [], False
            continue
        signal = str(r.get("signal", "")).lower().strip()
        it["signal"] = signal if signal in _VALID_SIGNAL else "low"
        tags = r.get("tags") or []
        it["tags"] = [t for t in tags if t in _TAGSET] if isinstance(tags, list) else []
        it["headline"] = str(r.get("headline") or "").strip() or it.get("title", "")
        it["synopsis"] = str(r.get("summary") or "").strip()
        it["impact"] = str(r.get("impact") or "").strip()
        it["author"] = str(r.get("author") or "").strip()
        inv = r.get("involved") or []
        it["involved"] = [str(x).strip() for x in inv if str(x).strip()][:6] if isinstance(inv, list) else []
        it["rumor"] = bool(r.get("rumor"))


def main() -> int:
    """本地自检：拉一小批真实条目 → 打分 → 打印（需 ANTHROPIC_API_KEY）。

        python -m radar.score          # 默认抓全量后取前 12 条试打
        python -m radar.score 6        # 自定义条数
    """
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12

    from radar.fetch import fetch_all

    items = fetch_all()[:n]
    print(f"抓到样本 {len(items)} 条，送 Haiku 打分中…\n")
    score_items(items)

    order = {"high": 0, "medium": 1, "low": 2}
    for it in sorted(items, key=lambda x: order.get(x["signal"], 9)):
        mark = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(it["signal"], "·")
        print(f"{mark} [{it['signal']:6s}] {it['source']}")
        print(f"   {it['title'][:80]}")
        print(f"   标签={it['tags']}  why={it['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
