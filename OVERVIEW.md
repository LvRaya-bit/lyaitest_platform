# LYAITEST 速查总览

> AI 驱动的测试智能体平台。自然语言驱动 Agent 执行接口测试、Web/App 自动化测试，支持录制/探索自动生成用例并产出报告。基于 FastAPI + Streamlit + LangGraph + Playwright + Appium。
> 详细文档见 [README.md](README.md)。

## 三段式架构

```
① 前端展现  Streamlit :8501   登录 + 9 个后台页（AI 助手 / 知识库 / 接口 / Web / App / 用例生成 / pi-Agent / 报告 / 用户）
② 后端编排  FastAPI  :8001     JWT 认证 / 路由 / 任务编排 / 落库（不直接驱动浏览器/设备）
③ 本地执行  Runner   :8765     真实驱动 Playwright / Appium，产物留 jobs/
④ 数据存储  SQLite              用户/会话/知识库/用例/探索任务/报告
```

## 三条关键约束

1. **执行必须在本机**：Playwright 要桌面会话，Appium 要真机/模拟器，云端代不了
2. **后端只编排**：`app/routers/*` 不跑测试，只校验/落库/投递 Runner/轮询回收
3. **`steps` 声明式 JSON 是唯一事实来源**：录制/探索/手编最终都收敛为同一份步骤数组（`navigate`/`click`/`input`/`assert`/`wait`/`screenshot`）

## 核心模块一句话

| 模块 | 一句话 |
|------|--------|
| AI 助手中心 | LangGraph 路由型 Agent：意图识别 → 7 节点之一（生成用例 / 跑接口 / 跑 Web / 查报告 / 闲聊） |
| 项目知识库 | 文档入库后作为 Agent 上下文（content 前 2000 字符注入 prompt） |
| 接口测试 | 接口定义/场景/步骤/环境；AI 助手可直接执行 |
| Web 自动化 | Playwright 录制(codegen)/探索(BFS)/预登录(storageState)/用例执行 |
| APP 自动化 | Appium + UiAutomator2 + Pytest；设备/探索/用例执行 |
| 用例生成 | 规则覆盖（八类维度）+ AI 逐条优化 + 可选 AI 补充；导出 CSV/Markdown/Pytest+POM/OpenAPI/Postman |
| pi-Agent 自愈 | 把跑不通的 Web 测试交给 pi-agent 闭环修复（详见 [pi_demos/selfheal/](pi_demos/selfheal/)） |
| 测试报告 | 各执行节点自动落库 `reports`，按用户隔离 |
| 用户管理 | 管理员可见，与 `/admin/*` 的 `require_admin` 一致 |

## 前端分层（P0/P1/P2 重构后）

- `app_frontend.py`（~360 行）：公共框架 + `main()` 用 `if/elif` + `state.current_page()` 调度
- `frontend/lib/`：`style` / `components` / `api` / `state` / `constants`（5 个公共模块）
- `frontend/pages/`：9 文件，1:1 对应导航项；页间单向依赖 `case_generation → web_automation`

> ⚠️ `fetch_data` / `_cached_get` / `state.token()` 依赖 `@st.cache_data` 与 `st.session_state`，**只在主线程可用**，禁止 `ThreadPoolExecutor` 并行调 `fetch_data`。

## 快速启动

```powershell
# 后端
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload

# 前端
streamlit run app_frontend.py

# 本地 Runner（Web/App 真实执行必需）
python local_runner/appium_runner.py --port 8765
```

环境变量：`DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `LOCAL_RUNNER_URL` / `JWT_SECRET_KEY`（详见 README 环境变量表）。

## 关键入口文件

| 入口 | 文件 |
|------|------|
| 后端 | `app/main.py` |
| 前端 | `app_frontend.py` → `frontend/` |
| Agent 引擎 | `app/services/agent.py`（LangGraph 8 节点） |
| 本地 Runner | `local_runner/appium_runner.py` |
| 录制转 steps | `local_runner/record_converter.py` |
| Pytest+POM 导出 | `app/services/web_codegen.py` |
| pi-Agent 自愈 | `pi_demos/selfheal/`（路线 A `heal_steps.py` / 路线 B `agents/`） |
