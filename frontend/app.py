#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【A · 前端与体验】Streamlit 主界面（框架占位，🚧 待开发）
─────────────────────────────────────────────────
规划板块（与团队分工对应）：
  1. 导入   —— 上传账单文件，调用 backend/bill_parser.py 解析入库
  2. 图表   —— 消费趋势 / 分类占比等图表展示
  3. 问答   —— AI 对话（调用 ai_docs/llm_client.py）
  4. 提醒   —— 消费提醒展示（LLM 生成）

UI 主题：浅红温馨风（网页版主题见 theme.css；Streamlit 主题待配置）。
实现后运行方式：pip install streamlit && streamlit run frontend/app.py
"""

TITLE = "AI Personal Finance Assistant"


def page_import():
    """导入板块占位。TODO(A组): 文件上传控件 + 调用 backend/bill_parser 解析 + 入库反馈。"""
    raise NotImplementedError("page_import 待实现（A·导入板块占位）")


def page_charts():
    """图表板块占位。TODO(A组): 消费趋势与分类占比图表。"""
    raise NotImplementedError("page_charts 待实现（A·图表板块占位）")


def page_qa():
    """问答板块占位。TODO(A组): 对话界面 + 调用 ai_docs/llm_client.ask。"""
    raise NotImplementedError("page_qa 待实现（A·问答板块占位）")


def page_reminders():
    """提醒板块占位。TODO(A组): 调用 ai_docs/llm_client.build_reminders 展示提醒。"""
    raise NotImplementedError("page_reminders 待实现（A·提醒板块占位）")


if __name__ == "__main__":
    # 仅作环境自检；界面由 streamlit run 启动（待实现）
    try:
        import streamlit  # noqa: F401

        print("Streamlit 已安装；界面待 A 组实现后用 streamlit run frontend/app.py 启动")
    except ImportError:
        print("Streamlit 未安装（pip install streamlit）；当前为 A 组前端占位文件")
