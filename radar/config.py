"""集中配置：路径、源加载、标签集、模型、推送阈值。

这些常量被 score / render / notify 等多个模块共用，先在骨架阶段定好，
后续模块直接 import，避免散落魔法值。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

# ── 路径 ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SOURCES_YAML = ROOT / "sources.yaml"
DB_PATH = ROOT / "radar.db"
DOCS_DIR = ROOT / "docs"
TEMPLATES_DIR = ROOT / "templates"


# ── .env 自动加载（纯标准库，无依赖）──────────────────────────
def _load_dotenv(path: Path) -> None:
    """把根目录 .env 里的 KEY=VALUE 读进 os.environ。

    - 真实(非空)环境变量优先：已 export / CI 注入的值不被覆盖。
      但环境里**已存在却为空**的变量（某些 shell/CI 会预置空串）视同未设，由 .env 填充。
    - 跳过空行与 # 注释；容忍 `export KEY=val` 前缀与首尾引号。
    - 文件不存在则静默跳过（CI 环境本就没有 .env）。
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and not os.environ.get(key):  # 未设或为空都用 .env 值
            os.environ[key] = val


_load_dotenv(ROOT / ".env")

# ── 打分模型 ──────────────────────────────────────────────────
SCORE_MODEL = "claude-haiku-4-5-20251001"
# 每次请求最多塞多少条（一次喂一批省钱，但太多会拖累输出可靠性）
SCORE_BATCH_SIZE = 40
SCORE_MAX_TOKENS = 8192

# ── 标签集（规格「信号分与标签」那节，顺序即看板筛选器顺序）──────
TAGS = [
    "关税",
    "出口管制",
    "制裁金融",
    "华盛顿政治",
    "高层互动",
    "新能源",
    "AI",
    "医药",
]

# ── 「人」线作者表：人物源(Substack/播客/博客)的默认作者 ─────────
# 打分时把作者作为 involved 字段的默认值喂给模型（按 feed 名匹配）。
SOURCE_AUTHORS = {
    "Sinocism": "Bill Bishop",
    "ChinaTalk": "Jordan Schneider",
    "Sinification": "Thomas des Garets Geddes",
    "Interconnected": "Kevin Xu",
    "Trivium China": "Trivium China (Andrew Polk / Trey McArver)",
    "The Wire China": "David Barboza",
    "Pekingnology": "Zichen Wang",
    "The China Lab": "Jeremy Wallace",
    "US-China Perception Monitor": "US-China Perception Monitor",
    "The East is Read": "Zichen Wang & Yuxuan Jia",
    "Ginger River Review": "Jiang Jiang",
    "Baiguan": "Baiguan",
    "Eye on China": "Manoj Kewalramani",
    "Tracking People's Daily": "Manoj Kewalramani",
    "Dan Wang": "Dan Wang",
    "Chartbook": "Adam Tooze",
    "Brad Setser (Follow the Money)": "Brad Setser",
    "Trade Talks": "Chad Bown",
    "Derisky Business": "Geoffrey Gertz & Emily Kilcrease",
    "Sinica Podcast": "Kaiser Kuo",
    "ChinaPower": "Bonny Lin",
    "Pekingology": "CSIS Pekingology",
    "Odd Lots": "Joe Weisenthal & Tracy Alloway",
    "Rhodium": "Rhodium Group",
}

# ── 人物头衔/机构（看板「人在说什么」板块展示，按 source=本人名 匹配）──
# 来源：解析账号时核实的 bio。覆盖全部 X(33) + Bluesky 活跃账号；缺的按
# person_type 兜底（见 person_title）。
PERSON_TITLES = {
    # reporter
    "Demetri Sevastopulo": "金融时报 美中记者",
    "Lingling Wei": "华尔街日报 首席中国记者",
    "Jenny Leonard": "彭博社 经济治国记者",
    "Ana Swanson": "纽约时报 贸易记者",
    "Keith Bradsher": "纽约时报 北京分社",
    "Alexandra Alper": "路透社 白宫/中国科技",
    "Edward Wong": "纽约时报 外交记者",
    "Bob Davis": "资深贸易记者(前华尔街日报)",
    "Phelim Kine": "Politico 外交记者",
    "Gavin Bade": "华尔街日报 贸易记者",
    "Eunice Yoon": "CNBC 北京分社",
    "Kana Inagaki": "金融时报 记者",
    "Eva Dou": "华盛顿邮报 科技记者",
    "Jeff Stein": "SpyTalk 创始编辑(国安/情报)",
    "Annmarie Hordern": "彭博电视 首席政治记者",
    # scholar
    "Brad Setser": "CFR 高级研究员",
    "Gregory Allen": "CSIS Wadhwani AI 中心",
    "Scott Kennedy": "CSIS 中国商业/经济",
    "Paul Triolo": "DGA-Albright 科技政策",
    "Chris Miller": "《芯片战争》作者/塔夫茨教授",
    "Rush Doshi": "CFR 中国战略/乔治城",
    "Bonnie Glaser": "GMF 印太项目主任",
    "Martin Chorzempa": "PIIE 高级研究员",
    "Dan Wang": "胡佛研究所/《Breakneck》作者",
    # vc_kol
    "Chamath Palihapitiya": "Social Capital 创始人",
    "David Sacks": "白宫 AI 与加密事务主管",
    "Jason Calacanis": "天使投资人/All-In 播客",
    "Balaji Srinivasan": "投资人/前 Coinbase CTO",
    "Bill Gurley": "Benchmark 风投",
    "Ben Thompson": "Stratechery 作者",
    # govt
    "John Moolenaar": "众议院对华特别委员会主席",
    # industry
    "Dylan Patel": "SemiAnalysis 创始人",
    "Kyle Chan": "布鲁金斯研究员",
    # Bluesky 独有（不在 X 名单）
    "Jordan Schneider": "ChinaTalk 主理人",
    "Bill Bishop": "Sinocism 主理人",
    "Adam Tooze": "哥伦比亚大学教授/Chartbook",
    "Chad Bown": "PIIE 高级研究员",
    "Ilaria Mazzocco": "CSIS 中国清洁技术",
    "Graham Webster": "斯坦福 DigiChina",
    "Helen Toner": "乔治城 CSET",
}

_PTYPE_FALLBACK = {
    "reporter": "记者", "scholar": "分析师", "vc_kol": "投资人",
    "industry": "产业分析", "govt": "官员",
}


def person_title(name: str, person_type: str = "") -> str:
    """取人物头衔；缺映射时按 person_type 兜底。"""
    return PERSON_TITLES.get(name) or _PTYPE_FALLBACK.get(person_type, "分析师")


# ── 来源类型分类（看板用）：官方 gov / 观点 view / 媒体 media ────
# media = 经 Google News 代理的官方机构与智库源（标题党/二手报道）。
# 其余 RSS（Substack/播客/博客/Rhodium 原生）= 观点；FR/Congress = 官方。
MEDIA_SOURCES = {
    "OFAC Recent Actions", "USTR Press", "House Select Committee on the CCP",
    "Senate Finance", "House Ways & Means",
    "PIIE", "CSIS China", "CNAS", "Brookings", "Carnegie", "Columbia CGEP",
}


def source_type(source: str | None) -> tuple[str, str]:
    """来源类型：官方 gov / 媒体 media / 观点 view。返回 (key, 中文标签)。"""
    s = source or ""
    if s.startswith("Federal Register") or s == "Congress.gov":
        return "gov", "官方"
    if s in MEDIA_SOURCES:
        return "media", "媒体"
    return "view", "观点"

# ── 推送：我关注的标签（命中才推 high 信号）。先设为全部 8 个 ────
# 想收窄推送范围时改这里即可（例如只留 ["关税", "出口管制"]）。
WATCHED_TAGS = list(TAGS)

# ── 数据保留窗口 ──────────────────────────────────────────────
RETENTION_DAYS = 30

# ── RSS 源分组（fetch 与 validate 共用的唯一清单）──────────────
# key = sources.yaml 里的顶层键；layer = source_layer（1 官方 / 2 智库人线）。
RSS_GROUPS = [
    {"key": "official_rss", "layer": 1, "label": "official_rss (官方机构 RSS)"},
    {"key": "substacks", "layer": 2, "label": "substacks (Substack · 域名+/feed)"},
    {"key": "think_tanks", "layer": 2, "label": "think_tanks (智库 RSS)"},
    {"key": "people_substacks", "layer": 2, "label": "people_substacks (个人 Newsletter)"},
    {"key": "blogs", "layer": 2, "label": "blogs (个人博客)"},
    {"key": "podcasts", "layer": 2, "label": "podcasts (播客 RSS)"},
]

# ── 拉取窗口 ──────────────────────────────────────────────────
# RSS / Congress 只收最近这么多天的条目（Federal Register 用 yaml 里的
# lookback_days）。主要作用：首跑时别把上百条陈旧条目灌进打分。
FETCH_LOOKBACK_DAYS = 7
# 每个 RSS feed 最多取多少条（Google News 一次能返回 100，需设上限兜底）
MAX_ITEMS_PER_FEED = 50

# ── 网络请求统一 User-Agent（不少机构 RSS 会拦默认 UA）──────────
USER_AGENT = "china-policy-radar/0.1 (+https://github.com/) feed-validator"
HTTP_TIMEOUT = 20


def load_sources() -> dict:
    """读取 sources.yaml。"""
    with open(SOURCES_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str, default: str | None = None) -> str | None:
    """读取环境变量（密钥统一走这里）。"""
    return os.environ.get(name, default)
