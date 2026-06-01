"""第三层 · X（一线记者/前官员「风声」）—— 本期不实现，仅占位。

第①②层跑顺后再接。接入步骤见 Claude_Code_搭建说明 §11：
  1. 选定取数方式（官方 X API 按量付费 / 第三方取数服务）。
  2. 实现下面的 fetch_x()，按 sources.yaml 的 x.handles 拉各账号最新 post。
  3. 产出与其他源一致的统一 Item（source_layer=3），交给 score 打分。
  4. sources.yaml 里把 x.enabled 置 true 并填 handles。
"""
from __future__ import annotations


def fetch_x(x_config: dict) -> list[dict]:
    """拉取 X handles 的最新 post，产出统一 Item 列表。

    本期不实现：x_config["enabled"] 为 false 时 fetch 流水线会跳过本源。

    TODO（接入 X 时）：
      - 遍历 x_config["handles"]，按所选取数方式拉最近 N 条 post。
      - 每条映射为统一 Item：
          {id, source, source_layer=3, title, summary, url, published}
        其中 id 用 post 的稳定唯一标识（如 tweet id 或 status url）。
      - 做超时与异常隔离，返回可能为空的 list。
    """
    raise NotImplementedError("第三层 X 本期未实现；见本文件 TODO 与搭建说明 §11。")
