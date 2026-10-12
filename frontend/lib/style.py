"""全局样式层：从 static/style.css 读取并注入到 Streamlit 页面。

仅依赖 streamlit + 标准库 os，无业务逻辑。
"""

import os

import streamlit as st


def inject_global_css():
    """注入全局样式：从 static/style.css 读取，缺失时回退为空（不影响功能）。

    路径计算：本文件位于 <root>/frontend/lib/style.py，向上两级到项目根目录，
    再拼接 static/style.css。原 app_frontend.py 中 __file__ 直接位于根目录，
    搬运到 lib 子包后必须显式回到根目录，否则找不到样式文件。
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    css_path = os.path.join(project_root, "static", "style.css")
    try:
        with open(css_path, "r", encoding="utf-8") as _f:
            _css = _f.read()
    except FileNotFoundError:
        _css = ""
    st.markdown(f"<style>{_css}</style>", unsafe_allow_html=True)
