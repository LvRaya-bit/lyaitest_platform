"""组件层：渲染骨架占位、统一卡片、蓝色隔断线。

仅依赖 streamlit，不依赖 api 层。
"""

import streamlit as st


def render_skeleton(n=3):
    """渲染 n 条骨架占位，配合 st.empty() 占位使用以减轻首屏白屏感。"""
    lines = "".join('<div class="skel-line"></div>' for _ in range(n))
    st.markdown(f'<div class="skel-block"></div>{lines}', unsafe_allow_html=True)


def render_card(title, body_md):
    """统一卡片组件：标题 + Markdown 正文，样式来自 static/style.css 的 .ucard。"""
    html = f'<div class="ucard"><div class="ucard-title">{title}</div><div class="ucard-body">{body_md}</div></div>'
    st.markdown(html, unsafe_allow_html=True)


def render_divider(tight=False):
    """蓝色渐变横线隔断，替代原来的空白留白气泡。tight=True 用于紧凑布局。"""
    cls = "divider tight" if tight else "divider"
    st.markdown(f'<div class="{cls}"></div>', unsafe_allow_html=True)
