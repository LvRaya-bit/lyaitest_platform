# 🤖 LYAITEST — AI驱动的测试智能体平台

**LYAITEST** 是一个基于 FastAPI + Streamlit 构建的智能测试平台。它通过自然语言驱动 Agent 执行接口测试、Web 自动化测试和 Android APP 自动化测试，支持从**录制/探索**自动生成测试用例，并自动记录测试报告。系统支持完整的用户认证机制，数据按用户隔离。

本项目为 UI 升级版（lyaitest_UI_MAX），基于 v0 设计标准对 Streamlit 前端进行了 **9 页面** 1:1 复刻整改，统一浅蓝简约后台风格，并修复了多项功能 Bug。

---

## ✨ UI 升级特性（lyaitest_UI_MAX 版本）

### 视觉与布局
- **统一配色**：页面底色 `#f8fafc` 浅蓝，主按钮 `#3b82f6` 天空蓝，白色细边框圆角卡片，禁用红色/黑色按钮
- **登录页**：左右分栏布局（左 38% 蓝色品牌宣传栏 / 右 62% 登录注册表单），垂直居中自适应窗口高度，修复注册 Tab 文字不显示问题
- **全局导航栏**：全部后台页面顶部横向导航（AI助手 / 知识库 / 接口测试 / Web自动化 / APP自动化 / 用例生成 / pi-Agent / 测试报告 / 用户管理），退出按钮置于右上角，侧边栏仅展示用户信息
- **AI 助手中心**：左侧会话管理 22% + 右侧 AI 对话 78%，右侧含机器人图标、快捷指令、白色圆角卡片、底部输入框+发送按钮、直接执行接口/Web测试按钮
- **接口测试 / Web 自动化**：素材管理、录制/探索与执行，列表条目点击可回填表单查看与编辑

### 功能增强
- **AI 会话管理**：支持自定义会话名称、删除会话、切换/修改绑定知识库
- **直接执行按钮**：AI 助手中可直接选择接口或 Web 用例一键执行，避免意图识别错乱
- **列表编辑回填**：接口测试 / Web 自动化列表点击条目自动回填表单，支持查看、编辑、更新
- **侧边栏折叠恢复**：修复侧边栏折叠后无法再次展开的问题

### Bug 修复
- 修复 AI 对话中执行接口/Web自动化测试失效问题（navigate 步骤真实发起 HTTP 请求）
- 修复 `save_report` 缺少 `user_id` 字段导致测试报告保存 500 错误
- 修复 AI 助手执行类型错乱（选了接口后再选 Web 用例仍执行接口测试）
- 修复接口测试/Web自动化页面编辑已创建用例报错（pending-load 模式）

### 自动化与用例生成能力矩阵

| 能力 | 实现方式 | 说明 |
|------|----------|------|
| Web 屏幕录制 | Playwright `codegen` + `record_converter.py` | 真人操作录制 → AST 转换为 `steps` 数组 → 直接可执行 |
| Web 自动探索 | Playwright 无头 BFS + JS DOM 枚举 | 默认深度 2、总页数 ≤80、单页点击 ≤25；同源/危险词拦截，产出 JSON、截图、DOM、storage_state |
| Web 预登录 | 有头浏览器 + `storageState` | 人工登录一次，保存 cookie/localStorage，探索与执行复用登录态 |
| Web 用例执行 | Playwright + `web_run_service` | 单条/用例集/批量执行，PASS/FAIL/SKIP 逐步骤回传 |
| APP（Android）自动化 | Appium + UiAutomator2 + Pytest | 本地 Runner 执行，平台后端不直接访问设备 |
| APP 自动探索 | Appium XML 解析 + 页面签名去重 | 支持 `auto`/`native`/`coordinate` 三种点击策略 |
| APP 自动登录 | 识别登录页控件 | 读取 Runner 进程环境变量中的测试账号，不落库 |
| 用例生成 | 规则覆盖 + AI 逐条优化 + 可选 AI 补充 | 9 列/P0-P3/8 类型；支持 Markdown/CSV、P0/P1 Pytest + Playwright + POM 导出 |

---

## 🎯 项目背景与目标

传统测试工具需要编写脚本或手动操作，学习成本高、效率低。LYAITEST 通过 AI Agent 实现"用对话驱动测试"，让测试人员用自然语言描述测试需求，Agent 自动解析意图、调用测试工具、执行并生成报告。

---

## 🏗️ 项目架构

平台采用“前端展现、后端编排、本地执行、数据持久化”的四层架构：前端只做展现，后端只做编排，真实浏览器/设备操作全部下沉到本机 Runner。

```
┌──────────────────────────────────────────────────────────────────────┐
│ ① 前端展现层   Streamlit  :8501                                       │
│   登录注册 │ AI助手 │ 知识库 │ 接口测试 │ Web自动化                    │
│   APP自动化 │ 用例生成 │ 测试报告 │ 用户管理                           │
├──────────────────────────────────────────────────────────────────────┤
│ ② 后端编排层   FastAPI  :8001（只编排，不直接操作浏览器/设备）          │
│   JWT认证 │ 路由分发 │ 任务编排 │ 结果回收 │ 落库                       │
├──────────────────────────────────────────────────────────────────────┤
│ ③ 本地执行层   Runner  :8765（真实驱动 Playwright / Appium）           │
│   接口测试 │ Web录制(codegen) │ Web探索(BFS) │ Web预登录(storageState)  │
│   Web用例执行 │ APP探索(UiAutomator2) │ APP用例执行(Pytest)             │
├──────────────────────────────────────────────────────────────────────┤
│ ④ 数据持久化   SQLite  :lyaitest.db                                   │
│   用户/会话/知识库/接口/Web用例/探索任务/生成用例/测试报告              │
└──────────────────────────────────────────────────────────────────────┘
```

### 四条关键设计约束

1. **执行必须在本机**：Playwright 需要桌面会话，Appium 需要连接真机/模拟器，因此浏览器与设备的真实驱动只能放在 `local_runner/`，Render / Streamlit Cloud 无法代劳。
2. **后端只做编排**：`app/routers/*` 不直接跑测试，只负责认证、参数校验、任务落库、调用 `local_runner_client.py` 投递任务，再轮询回收结果。
3. **声明式步骤是执行边界**：Web 录制、Web 探索、手工编辑最终收敛为同一份 `steps` JSON（`navigate`/`click`/`input`/`assert`/`wait`/`screenshot`）；Web 用例生成时，规则层生成的 executable steps 和 locator 是受保护事实，不由 AI 改写。
4. **规则先行、AI 可降级**：APP/Web 用例先依据模板规则生成完整覆盖初稿，再由 AI 按模板逐条优化；AI 无 Key、超时或输出异常时，完整回退到规则结果，不阻断生成。

### 核心数据流

```text
Web/APP 探索证据
  ├─ Web：URL/title/h1/DOM/locator/表单/请求失败/截图/storage_state
  └─ APP：页面 XML/控件描述/bounds/页面签名/截图
             │
             ▼
规则用例生成（八类测试维度、P0-P3、证据约束）
             │
             ▼
AI 逐条优化（case_id 一一对应，保留 Web executable steps）
             │  AI 失败 → 保留规则初稿
             ▼
校验与持久化（generated_case_sets / generated_test_cases / web_cases）
             │
             ├─ 云效 9 列 CSV/Markdown
             └─ P0/P1 → Pytest + Playwright + POM + 截图/Trace
```

### 核心模块说明

| 模块 | 技术栈 | 职责 |
|------|--------|------|
| 前端界面 | Streamlit | 登录页 + 9 个后台页面（AI 助手 / 知识库 / 接口测试 / Web 自动化 / APP 自动化 / 用例生成 / pi-Agent / 测试报告 / 用户管理），代码按 `frontend/lib` + `frontend/pages` 分层 |
| API 网关 | FastAPI | 处理 HTTP 请求，JWT 认证，路由分发，任务编排 |
| Agent 引擎 | LangGraph + LangChain | 意图识别、状态管理、工具调用编排 |
| 用户认证 | JWT + bcrypt | 用户注册、登录、Token 验证、数据隔离 |
| 接口测试 | requests | 执行接口/场景测试，支持多步骤场景与环境变量 |
| Web 自动化 | Playwright（Runner 侧） | 屏幕录制、自动探索、预登录、用例批量执行 |
| Android 测试 | Appium + UiAutomator2 + Pytest | 设备环境检测、登录、探索、用例执行 |
| 用例生成 | 规则覆盖 + AI 逐条优化 + AI 可选补充 | APP/Web 共用质量校验；保持 9 列、P0-P3、8 类型，支持导出与批量执行 |
| 本地 Runner | Python HTTP Server（8765） | 真实驱动浏览器与设备，产物留存 `jobs/` |
| 数据存储 | SQLite | 保存用户、会话、知识库、用例、探索任务、测试报告 |

### 🧠 Agent 引擎设计

AI 助手中心的后端实现位于 [`app/services/agent.py`](app/services/agent.py)，基于 **LangGraph `StateGraph`** 构建一个**路由型 Agent**（非 ReAct 循环）：单个入口节点做意图识别，按 `next_step` 字段条件路由到 7 个执行节点之一，所有执行节点直达 `END`。

#### 状态结构

`AgentState`（TypedDict）在节点间传递：

| 字段 | 类型 | 用途 |
|------|------|------|
| `messages` | list | 对话历史，末条为当前用户输入 |
| `next_step` | str | 意图识别输出，决定下一跳节点 |
| `session_id` | str | 会话 ID，用于落库关联 |
| `knowledge_base_id` | Optional[str] | 绑定的知识库，供检索注入 |
| `interface_id` | Optional[str] | 前端选择器直传的接口 ID |
| `web_case_id` | Optional[str] | 前端选择器直传的 Web 用例 ID |
| `user_id` | Optional[str] | 用户 ID，数据隔离与落库 |

#### LLM 配置

智谱 GLM-4（`temperature=0.3`），意图识别、用例生成、普通对话共用同一实例。Key 通过 `ZHIPU_API_KEY` 环境变量注入。

#### 节点职责

| 节点 | 触发方式 | 职责 |
|------|---------|------|
| `identify_intent`（入口） | 必经 | 读末条消息 → 输出 `next_step`；若前端已传 `interface_id` / `web_case_id` 则**跳过 LLM** 直跳对应执行节点 |
| `generate_case` | 自然语言 | 联动知识库（文档 content 前 2000 字符）+ 已录入接口/Web 用例作参考，输出结构化用例 |
| `run_interface` | 前端选择器 | 按 `interface_id` 取接口定义，`requests` 真实执行，落 `reports` |
| `run_api_test` | 自然语言 | 按用户消息匹配已录入接口名/id，找不到则取最新一条，执行并落库 |
| `run_web_case` | 前端选择器 | 按 `web_case_id` 取步骤，提交本机 Runner 真实执行 + 轮询回读 |
| `run_web_test` | 自然语言 | 按用户消息匹配已录入 Web 用例，找不到则取最新一条 |
| `query_report` | 自然语言 | 查最近 10 条报告，汇总通过率与最近 5 条明细 |
| `chat` | 自然语言 | 联动知识库的普通对话 |

#### 路由优化：跳过意图识别

前端「直接执行接口」「直接执行 Web 用例」按钮会把 `interface_id` / `web_case_id` 直传 Agent。`identify_intent` 检测到这两个字段时**不调用 LLM**，直接把 `next_step` 置为 `run_interface` / `run_web_case`。这避免了"用户选了接口、消息里又提到 web、意图被识别成 web 测试"这类错乱（即 UI 升级特性里提到的"执行类型错乱"修复）。

#### Web 执行链路

`run_web_case` / `run_web_test` 共用 `_execute_web_steps`：

```
submit_web_run(report_id, 用例)
  → create_running_report(占位 reports 行，status=running)
  → 轮询 collect_web_run（2s 间隔，120s 超时）
  → 汇总 PASS / FAIL / SKIP 逐步骤日志
```

Runner 不可用时 `report_id=None`、全部步骤记为失败；超时返回"仍在运行"提示，最终结果可在【测试报告】页查看。

#### 落库

- 每个执行节点成功后 `save_report`（含 `status_code` / `response_time` / `passed_cases` / `failed_cases` 等）
- `run_agent` 末尾把 user 消息 + 末条 assistant 回复写入 `messages` 表（会话持久化）

---

### 前端工程内部结构（frontend/ 包 + app_frontend.py）

前端经三阶段重构（P0 抽公共 helper / P1 多页面拆分 / P2 state 集中）后，`app_frontend.py` 从 2400+ 行降到 ~360 行，仅保留公共框架；业务代码全部下沉到 `frontend/` 包，形成"公共层 + 页面层"清晰分层。

#### 公共层 `frontend/lib/`（5 模块）

| 模块 | 关键导出 | 职责 |
|------|---------|------|
| `style.py` | `inject_global_css()` | 读取 `static/style.css` 注入全局样式；按 `__file__` 上溯两级定位项目根 |
| `components.py` | `render_skeleton / render_card / render_divider` | 首屏骨架占位、统一卡片、蓝色渐变隔断线 |
| `api.py` | `API_BASE / get_headers / fetch_data / call_api / clear_user_cache / _api_detail` | HTTP 调用含 30s TTL 缓存（`_cached_get`）、401 自动重登、写操作后自动清缓存；`_api_detail` 为 5 个页面共用的错误详情提取 |
| `state.py` | `init_state / token / username / user_id / is_logged_in / is_admin / set_identity / current_page / set_page / logout / clear_user_selections` | 跨页共享核心 key（身份/路由/选择编辑/消息/web 结果）集中初始化与访问；页内临时状态仍直接用 `st.session_state` |
| `constants.py` | `_RUNNER_START_COMMAND / _RUNNER_HINT / _WEB_RUN_POLL_*` | 跨页共用常量（Runner 提示文案、Web 执行轮询参数） |

#### 页面层 `frontend/pages/`（9 文件，1:1 对应导航项）

| 文件 | 对应导航项 | 说明 |
|------|-----------|------|
| `ai_assistant.py` | AI助手中心 | 会话管理 + AI 对话 |
| `knowledge_base.py` | 项目知识库 | 知识库与文档 |
| `api_test.py` | 接口测试 | 接口定义 + 单接口执行 |
| `web_automation.py` | Web自动化 | 录制/探索/预登录/用例执行（最大，含 `_run_generated_cases_batch`） |
| `app_automation.py` | APP自动化 | Android 设备/探索/执行 |
| `case_generation.py` | 用例生成 | 八类维度生成 + 导出（含 `render_case_export_button`） |
| `pi_agent.py` | pi-Agent | 自愈任务（含 4 个 `_render_pi_agent_*` helper + `_PI_AGENT_*` 常量） |
| `report.py` | 测试报告 | 报告查询/删除 |
| `user_management.py` | 用户管理 | 管理员用户管理 |

#### 依赖约束

- `app_frontend.py` 顶部 `st.set_page_config` 必须是首个 `st` 调用，不能搬到 lib
- `main()` 仍用 `if/elif` + `state.current_page()` 调度，仅 `render_*` 函数从 pages import
- 页间单向依赖：`case_generation` → `web_automation`（`import _run_generated_cases_batch`，因批量执行复用 web 运行结果渲染）；其余页面互不依赖
- `state.py` 只管跨页共享核心 key，页内临时 UI 状态（`web_login_state` / `wc_steps_rows` / `if_*` 等）仍直接用 `st.session_state`

> ⚠️ 线程约束：`fetch_data` / `_cached_get` / `state.token()` 依赖 `@st.cache_data` 与 `st.session_state`，二者均需 Streamlit 的 `ScriptRunContext`，**只在主线程可用**。禁止用 `ThreadPoolExecutor` 并行调 `fetch_data`——子线程拿不到 token 会 401、缓存也会失效。

---

## 📁 项目目录结构

```
lyaitest_UI_MAX/
├── app/
│   ├── routers/                    # API 路由层（只编排，不跑测试）
│   │   ├── auth.py                 # 注册 / 登录 / 当前用户
│   │   ├── admin.py                # 用户管理（管理员）
│   │   ├── sessions.py             # 会话与消息管理
│   │   ├── agent.py                # Agent 对话入口
│   │   ├── report.py               # 测试报告查询 / 删除
│   │   ├── knowledge_base.py       # 知识库与文档
│   │   ├── api_test.py             # 接口定义 / 场景 / 步骤 / 环境
│   │   ├── web_automation.py       # Web 录制 / 探索 / 预登录 / 用例 / 执行
│   │   ├── app_automation.py       # Android 设备 / 探索 / 用例执行
│   │   ├── case_generation.py      # 用例生成 / 批量操作 / 导出
│   │   └── pi_agent.py              # pi-Agent 自愈任务
│   ├── services/                   # 业务逻辑层
│   │   ├── agent.py                # Agent 核心逻辑（LangGraph）
│   │   ├── auth_service.py         # 认证服务与默认管理员
│   │   ├── report_service.py       # 测试报告落库
│   │   ├── case_generation.py      # 用例生成（规则覆盖 + AI 逐条优化 + AI 补充）
│   │   ├── web_codegen.py          # Pytest + Playwright + POM 代码包生成
│   │   ├── web_run_service.py      # Web 执行任务编排与结果回收
│   │   └── local_runner_client.py  # 本地 Runner HTTP 客户端
│   ├── dependencies/auth.py        # JWT 验证依赖注入
│   ├── models/                     # 数据模型（session.py / report.py）
│   ├── database.py                 # SQLite 连接与建表
│   └── main.py                     # FastAPI 应用入口
├── app_frontend.py                 # Streamlit 前端入口（公共框架 + main 调度，~360 行）
├── frontend/                       # 前端业务代码包（P0/P1/P2 重构后从 app_frontend.py 抽出）
│   ├── lib/                        # 公共层
│   │   ├── style.py                # inject_global_css 注入 static/style.css
│   │   ├── components.py            # render_skeleton / render_card / render_divider
│   │   ├── api.py                  # API_BASE / fetch_data / call_api / _api_detail / clear_user_cache
│   │   ├── state.py                # init_state + 身份/路由 getter/setter + logout
│   │   └── constants.py            # _RUNNER_* / _WEB_RUN_POLL_* 跨页常量
│   └── pages/                      # 页面层（1:1 对应导航项）
│       ├── ai_assistant.py         # AI助手中心
│       ├── knowledge_base.py       # 项目知识库
│       ├── api_test.py             # 接口测试
│       ├── web_automation.py       # Web自动化（含 _run_generated_cases_batch）
│       ├── app_automation.py       # APP自动化
│       ├── case_generation.py      # 用例生成（含 render_case_export_button）
│       ├── pi_agent.py             # pi-Agent 自愈
│       ├── report.py               # 测试报告
│       └── user_management.py      # 用户管理
├── static/
│   └── style.css                   # 全局样式（设计 Token + 卡片/骨架/隔断等组件类）
├── local_runner/
│   ├── appium_runner.py            # 本地 Runner（Playwright + Appium，:8765）
│   ├── record_converter.py         # codegen 脚本 → steps 数组 的 AST 转换
│   ├── README.md                   # Runner 安装与启动说明
│   └── jobs/                       # 运行产物：截图、XML、探索结果 JSON、Trace
├── pi_demos/                       # pi-agent 学习与自愈示例
├── requirements.txt                # 主依赖（平台 + Playwright）
├── requirements-appium.txt         # Android/Appium 依赖
├── render.yaml                     # Render 部署配置
├── Docker.md                       # 容器化部署说明
├── 本地模型部署.md                  # 本地模型接入说明
├── .streamlit/config.toml          # Streamlit 主题配置
├── .env                            # 环境变量（不上传 GitHub）
└── README.md                       # 项目文档
```

---

## ⚙️ 技术栈

| 类别 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 前端框架 | Streamlit |
| Agent 引擎 | LangGraph + LangChain |
| 大模型 | DeepSeek / 智谱 GLM |
| 用户认证 | JWT (python-jose) + bcrypt |
| 接口测试 | requests |
| Web 自动化 | Playwright（录制 codegen / 探索 / 执行） |
| Android 自动化 | Appium + UiAutomator2 + Pytest |
| 数据库 | SQLite |
| 部署平台 | Render (后端) + Streamlit Cloud (前端) |

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/LvRaya-bit/lyaitest_UI_MAX.git
cd lyaitest_UI_MAX
```

### 2. 创建虚拟环境

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
python -m playwright install
```

### 4. 配置环境变量

创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=your_deepseek_api_key
ZHIPU_API_KEY=your_zhipu_api_key   # 如果用智谱
```

### 5. 启动后端服务

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

后端默认运行在 http://127.0.0.1:8001

### 6. 启动前端界面

```bash
streamlit run app_frontend.py
```

前端默认运行在 http://localhost:8501

### 7. 使用系统

1. 打开 http://localhost:8501
2. 注册新用户或登录已有账号
3. 登录成功后进入 AI 助手中心开始使用

### 8. 启动本地 Runner（Web / Android 执行必需）

后端和 Streamlit 可以继续按上面的方式启动；需要录制、探索或执行真实浏览器/设备时，
在本机额外启动 Runner：

```powershell
python local_runner/appium_runner.py --port 8765
```

平台通过 `LOCAL_RUNNER_URL` 调用本地 Runner，默认地址为 `http://127.0.0.1:8765`。

只需要 Web 自动化时，`requirements.txt` 与 `playwright install` 已足够；需要
Android 自动化时，再补充安装 Appium 相关依赖：

```powershell
pip install -r requirements-appium.txt
npm install -g appium
appium driver install uiautomator2
appium --port 4723
```

在“APP 自动化”页面配置设备 UDID、包名和 Activity，通过环境检测后再开始探索或执行测试。

#### Android 设备配置

在“APP 自动化”页面新增设备时填写：

| 字段 | 示例 | 说明 |
|------|------|------|
| 设备 UDID | `emulator-5556` | 使用 `adb devices` 获取 |
| App 包名 | `com.example.app` | 使用 `adb shell dumpsys window` 或 APK 信息获取 |
| App Activity | `com.example.MainActivity` | 记录当前前台 Activity |
| Appium Server | `http://127.0.0.1:4723` | 本机 Appium 地址 |
| 测试手机号 | 可选 | 仅用于自动登录 |
| 密码环境变量名 | `TEST_PASSWORD` | 只填写变量名，不填写真实密码 |

可先点击“环境检测”，确认 Appium、ADB、设备和 App 包均可用。

#### 相关 API（Runner 健康检查）

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/v1/app-automation/runner/health` | 检查本地 Runner 是否在线 |

### 8.1 Windows 测试账号环境变量配置

页面中的“密码环境变量名”只填写变量名，默认填写 `TEST_PASSWORD`，不要填写真实密码。
真实手机号、密码和设备 UDID 需要在启动 Runner 前设置，并且必须和 Runner 在同一个终端窗口中执行。

如果当前终端提示符类似 `D:\...>`，使用 Windows CMD 写法：

```cmd
set "TEST_PHONE=你的手机号"
set "TEST_PASSWORD=你的密码"
set "DEVICE_UDID=你的设备UDID"
python local_runner\appium_runner.py --port 8765
```

如果使用 PowerShell，使用下面的写法：

```powershell
$env:TEST_PHONE = "你的手机号"
$env:TEST_PASSWORD = "你的密码"
$env:DEVICE_UDID = "你的设备UDID"
python local_runner/appium_runner.py --port 8765
```

不要在 CMD 中使用 `$env:TEST_PASSWORD`，也不要在 PowerShell 中使用 `set TEST_PASSWORD=...`。
可分别使用 `echo %DEVICE_UDID%`（CMD）或 `$env:DEVICE_UDID`（PowerShell）检查变量是否生效。

> 安全提示：测试账号只应通过本地 Runner 进程环境变量传入，不要写入 README、源码、数据库或提交到 Git。

---

## 🕸️ Web 自动化

Web 自动化有 4 类操作入口，都在“Web自动化”与“用例生成”页面完成。

### 1. 屏幕录制 → 生成可执行用例

真人操作浏览器，系统自动把操作转成平台可执行的 `steps` JSON：

| 步骤 | 说明 |
|------|------|
| 开始录制 | `POST /api/v1/web-automation/recordings`，Runner 用 `playwright codegen` 打开**有头**浏览器 |
| 人工操作 | 在浏览器里正常点击/输入，录制必须有头运行，只能在本机桌面会话使用 |
| 结束录制 | **关闭浏览器窗口**即结束；窗口卡死时可用 `POST /recordings/{id}/stop` 强制终止（会丢失本次内容） |
| 自动转换 | `record_converter.py` 用 AST 解析 codegen 脚本，产出 `steps` 数组与需要人工确认的 `warnings` |
| 载入编辑器 | 前端轮询 `GET /recordings/{id}` 拿到 `steps`，写入步骤列表编辑器，可直接执行 |

### 2. 自动探索（只读）

`POST /api/v1/web-automation/explorations`，Runner **无头**打开站点做 BFS 探索：

- **同源限制**：不跨越域名
- **只读优先**：默认识别并拦截表单提交按钮；开启“允许提交表单”后按白名单放行登录/查询等低风险动作，危险词（删除/支付/导出/上传等）与跨域链接始终拦截
- **浏览器模式**：支持无头后台探索和有头可视化探索；有头模式可直接观察点击过程
- **可停止**：运行中的探索可点击“停止探索”，已生成的 JSON、截图、DOM 和登录态会保留
- **页面签名去重**：`域名 + 路径 + title + h1 + 稳定元素标签`；表单提交后即使 URL 不变，只要 DOM 签名变化也会继续探索
- **上限保护**：默认深度 2、`max_pages ≤ 80`、`max_actions_per_page ≤ 25`、总动作 `max_actions ≤ 1000`

产物结构：

```text
local_runner/jobs/<任务ID>/
├── <探索结果 JSON>    # pages / elements / edges
├── *.png              # 各页面截图
├── *.html             # 各页面 DOM 快照
└── storage_state.json # 探索结束时的登录态快照
```

### 3. 预登录（推荐）

需要登录才能看到的页面，用 `storageState` 复用登录态，避免把账号密码写进脚本：

| 步骤 | 接口 | 说明 |
|------|------|------|
| 打开登录页 | `POST /login` | Runner 打开**有头**浏览器到目标地址 |
| 人工登录 | — | 在浏览器里手动完成登录（含验证码等） |
| 保存登录态 | `POST /login/{job_id}/save` | 写出 `storage_state.json`（cookie + localStorage） |
| 复用 | 探索/执行时传入 `storage_state` | 无头运行也带登录态，不会看到登录页 |

未保存前可 `POST /login/{job_id}/cancel` 取消。

### 4. 用例与执行

- `web_cases`：单条用例，`steps` 存可执行步骤数组
- `web_suites`：用例集
- `web_tasks`：按用例集创建的执行任务（支持定时表达式字段）

执行结果逐步骤返回 `PASS / FAIL / SKIP`，失败会保留截图；`GET /runs/{report_id}` 轮询进度。

> 注意：探索与执行都使用 `browser.new_context(ignore_https_errors=True)`，与录制侧行为一致，
> 否则证书异常站点会整批失败。

### 相关 API

| 方法 | 路径 | 用途 |
|------|------|------|
| `POST` | `/api/v1/web-automation/recordings` | 开始屏幕录制 |
| `GET` | `/api/v1/web-automation/recordings/{id}` | 查询录制结果与 steps |
| `POST` | `/api/v1/web-automation/recordings/{id}/stop` | 强制终止录制 |
| `GET` | `/api/v1/web-automation/recordings/active` | 查询未结束的录制 |
| `POST` | `/api/v1/web-automation/login` | 打开有头浏览器预登录 |
| `POST` | `/api/v1/web-automation/login/{id}/save` | 保存 storageState |
| `POST` | `/api/v1/web-automation/login/{id}/cancel` | 取消预登录 |
| `GET/POST` | `/api/v1/web-automation/explorations` | 查询 / 启动网页探索 |
| `GET/DELETE` | `/api/v1/web-automation/explorations/{id}` | 查询 / 删除探索任务 |
| `GET` | `/api/v1/web-automation/explorations/{id}/artifacts` | 列出探索产物 |
| `GET` | `/api/v1/web-automation/explorations/{id}/artifacts/{name}` | 下载截图/DOM/JSON/登录态 |
| `POST` | `/api/v1/web-automation/explorations/{id}/stop` | 停止运行中的探索并保留产物 |
| `GET/POST` | `/api/v1/web-automation/cases` | Web 用例列表 / 新建 |
| `POST` | `/api/v1/web-automation/cases/{id}/run` | 执行单条用例 |
| `GET` | `/api/v1/web-automation/runs/{report_id}` | 轮询执行进度 |
| `POST` | `/api/v1/web-automation/suites/{id}/run` | 执行用例集 |

---

## 🧭 APP 自动化

### App 自动探索

探索流程为：启动 App → 检测登录页 → 自动登录 → 获取 XML → 本地解析可点击元素 →
页面签名去重 → 点击控件 → 判断页面变化 → 深度优先探索 → 保存结果。

探索结果保存在：

```text
local_runner/jobs/<任务ID>/
├── exploration_result.json
├── 000_*.xml
└── 000_*.png
```

点击策略说明：

| 策略 | 适用场景 |
|------|--------|
| `auto` | 默认策略，按页面缓存原生控件，兼顾速度和 MuMu 多显示兼容性 |
| `native` | 使用 Appium 原生控件操作，适合页面 XML 与截图显示不一致的模拟器 |
| `coordinate` | 使用 XML bounds 中心点坐标点击，适合标准单显示设备，速度最快 |

探索默认会过滤无描述的空控件、不可见控件、重复控件和退出/删除/注销等危险操作。
最大深度和最大点击次数可在页面中设置。

### 探索前自动登录

Runner 会识别登录页的手机号输入框、密码输入框、用户协议复选框和提交按钮，自动完成登录。
账号信息只从 Runner 所在终端的环境变量读取，不会写入平台数据库。若账号变量未配置，
任务不会伪造登录成功，会在探索结果中显示跳过原因。

### 相关 API

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/v1/app-automation/runner/health` | 检查本地 Runner |
| `GET/POST` | `/api/v1/app-automation/devices` | 设备列表 / 新增设备 |
| `PUT/DELETE` | `/api/v1/app-automation/devices/{id}` | 更新 / 删除设备 |
| `POST` | `/api/v1/app-automation/devices/{id}/preflight` | 检查 Appium、ADB、设备和 App |
| `GET/POST` | `/api/v1/app-automation/explorations` | 查询 / 启动探索任务 |
| `GET/DELETE` | `/api/v1/app-automation/explorations/{id}` | 查询 / 删除探索结果 |
| `GET/POST` | `/api/v1/app-automation/test-runs` | 查询 / 创建用例执行任务 |
| `GET` | `/api/v1/app-automation/test-runs/{id}` | 查询执行详情 |

---

## 📝 测试用例生成

进入“用例生成”页面，有 4 个来源标签页：**APP功能文档 / App探索结果 / Web探索结果 / 接口文档**。

### 生成方式：规则覆盖 + AI 逐条优化 + 可选 AI 补充

APP/Web 先按对应 Prompt 模板的**八类测试维度**生成完整规则初稿，不依赖任何模型 Key；
配置模型后，再将规则用例分批交给 AI 按模板逐条优化。AI 通过 `case_id` 与原用例一一对应，
不会合并、删除或凭空替换用例；Web 的 Playwright executable steps 和 locator 始终由规则层保护。

| 维度 | 覆盖内容 |
|------|----------|
| 功能测试·正常流 | 页面加载、代表性入口点击、导航往返一致性 |
| 功能测试·异常流 | 空值/特殊字符/超长文本/非法格式等异常输入与错误处理 |
| 功能测试·边界值 | 列表为空、仅 1 条、超过一页，数值上下限 |
| 功能测试·业务规则 | 离线/未登录/权限不足等状态流转与数据一致性 |
| 性能测试 | 页面加载与操作响应耗时 |
| 易用性测试 | 加载中、空状态、错误提示、操作反馈 |
| 稳定性测试 | 断网/弱网、切后台恢复、重复进入、长时间运行 |
| 安全性测试 | 越权访问、敏感信息明文、权限控制 |
| 兼容性测试 | 不同系统版本、屏幕尺寸/分辨率（全局 3-5 条） |
| 自动化测试 | 脚本可重复执行、元素定位稳定性（全局 2-3 条） |

> 模板规则中「接口测试」在探索结果没有接口证据时按规则跳过，不编造接口。

AI 层在配置 `ZHIPU_API_KEY` 或 `DEEPSEEK_API_KEY` 后启用，包含“逐条优化”和“证据驱动补充”两步：
APP 探索最多补充 40 条、APP 功能文档最多补充 20 条、Web 探索最多补充 8 条；补充用例同样必须
通过证据与可执行性校验，并且 Web 只允许使用探索证据中出现过的页面与定位器。AI 无 Key、超时或
格式异常时，规则初稿完整保留。

可通过以下配置关闭 AI 调用（仅用规则用例）：

```env
CASE_GENERATION_USE_LLM=false
```

### 数量与覆盖控制

| 保护项 | 值 | 说明 |
|--------|----|------|
| 规则用例上限 | APP 探索 60 条；Web 探索 100 条 | 规则层优先保障模板覆盖，入口按代表性收敛 |
| AI 补充上限 | APP 探索 40 条；APP 文档 20 条；Web 探索 8 条 | 仅补充证据驱动场景，不替代规则初稿 |
| 每页入口数 | 3 个 | 同类入口只保留代表性用例，额度留给异常/边界/业务规则 |
| P0 占比 | ≤ 10% | P0 只给登录与核心主流程 |
| 重复生成 | 覆盖而非累积 | 同一探索任务再次生成，会先清理上一次的用例集与 Web 副本 |

### 批量操作与导出

- **批量执行**：勾选多条用例 → 一键提交执行，逐步骤回传结果（复用 `web_run_service`）
- **批量删除**：勾选多条用例 → 删除，并联动清理 Web 自动化里的可执行副本
- **删除用例集**：整集删除，Web 探索来源的会同步清理关联的 `web_cases`
- **导出**：CSV/Markdown 云效 9 列（全部来源）；Web 用例支持 P0/P1 Pytest + Playwright + POM 压缩包；接口文档额外支持 OpenAPI JSON 与 Postman JSON

生成完成后，在“已生成用例集”中点击“准备 CSV”再点击“下载 CSV”。不要直接在浏览器打开
相对 API 地址，因为导出接口需要当前登录 Token。

### 输出字段

云效导出固定为 9 列：标题、目录、负责人、前置条件、步骤描述、预期结果、关联工作项、优先级、类型。
内部存储另外保留来源、自动化状态、生成方式和 Web 可执行步骤等元数据。
优先级限定为 `P0/P1/P2/P3`，测试类型限定为平台支持的八类测试类型。

### 相关 API

| 方法 | 路径 | 用途 |
|------|------|------|
| `POST` | `/api/v1/case-generation/function-document` | 从 APP 功能文档生成用例 |
| `POST` | `/api/v1/case-generation/exploration/{id}` | 从 App 探索结果生成用例 |
| `POST` | `/api/v1/case-generation/web-exploration/{id}` | 从 Web 探索结果生成用例（双写 web_cases） |
| `POST` | `/api/v1/case-generation/api-document` | 从接口 Markdown 生成用例 |
| `GET` | `/api/v1/case-generation/sets` | 用例集列表 |
| `GET` | `/api/v1/case-generation/sets/{id}` | 用例集详情 |
| `DELETE` | `/api/v1/case-generation/sets/{id}` | 删除整个用例集 |
| `PATCH` | `/api/v1/case-generation/cases/{id}` | 编辑单条用例 |
| `POST` | `/api/v1/case-generation/cases/batch-delete` | 批量删除用例 |
| `POST` | `/api/v1/case-generation/cases/batch-run` | 批量执行用例 |
| `GET` | `/api/v1/case-generation/sets/{id}/export.csv` | 导出 CSV |
| `GET` | `/api/v1/case-generation/sets/{id}/export.md` | 导出 Markdown 9 列 |
| `GET` | `/api/v1/case-generation/sets/{id}/export.pytest.zip` | 导出 P0/P1 Pytest + Playwright + POM |
| `GET` | `/api/v1/case-generation/sets/{id}/export.openapi.json` | 导出 OpenAPI JSON |
| `GET` | `/api/v1/case-generation/sets/{id}/export.postman.json` | 导出 Postman JSON |

上述接口均需要登录后的 Bearer Token，推荐通过平台页面操作，不要直接复制相对路径到浏览器地址栏。

---

## 🌐 上线部署（Render + Streamlit Cloud）

### 后端部署（Render）

1. 在 [Render](https://render.com) 创建 Web Service
2. 连接 GitHub 仓库 `lyaitest_upgrade`
3. Render 会自动识别 `render.yaml` 配置：
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port 10000`
4. 在 Render Dashboard → Environment 中配置：
   - `DEEPSEEK_API_KEY` = 你的 DeepSeek API Key
   - `JWT_SECRET_KEY` = 随机生成的密钥（用于JWT签名）
5. Render 会自动挂载 1GB 磁盘到 `/data`，SQLite 数据库保存在 `/data/lyaitest.db`

获取后端域名：`https://lyaitest-backend-xxxx.onrender.com`

> Web 自动化与 Android Appium 功能都需要 Runner 能访问本机桌面会话与 Android 设备。
> Render/Streamlit Cloud 上的服务无法直接访问开发电脑的 `127.0.0.1` 和本地模拟器；
> 本地调试时建议让 FastAPI、Streamlit、Runner、Appium 在同一台电脑运行。
> 若必须云端调度，应通过受保护的网络通道连接 Runner，并配置 `LOCAL_RUNNER_TOKEN`，
> 不要直接暴露 8765 端口。

### 前端部署（Streamlit Cloud）

1. 在 [Streamlit Cloud](https://streamlit.io/cloud) 创建 App
2. 连接同一个 GitHub 仓库
3. 在 App Settings → Secrets 中添加：
   ```toml
   api_base = "https://lyaitest-backend-xxxx.onrender.com/api/v1"
   ```
4. 部署完成后访问 Streamlit Cloud 提供的域名

### 环境变量说明

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 必填 |
| `ZHIPU_API_KEY` | 智谱 API 密钥，用于 AI 用例增强 | 可选 |
| `CASE_GENERATION_USE_LLM` | 是否启用 AI 用例增强 | `true` |
| `CASE_GENERATION_MODEL` | 用例生成模型名称 | 按服务默认值 |
| `JWT_SECRET_KEY` | JWT 签名密钥 | 自动生成 |
| `DATABASE_PATH` | SQLite 数据库路径 | `lyaitest.db` |
| `API_BASE` | 前端调用的后端地址 | `http://localhost:8001/api/v1` |
| `LOCAL_RUNNER_URL` | FastAPI 调用本地 Runner 的地址 | `http://127.0.0.1:8765` |
| `LOCAL_RUNNER_TOKEN` | 后端请求 Runner 的认证令牌 | 空 |
| `RUNNER_TOKEN` | Runner 侧校验的令牌（与上面保持一致） | 空 |
| `RUNNER_ALLOWED_ROOTS` | 允许执行 Pytest 的目录根（分号分隔） | 空 |
| `TEST_PHONE` | Android 测试账号手机号 | 运行 Runner 前配置 |
| `TEST_PASSWORD` | Android 测试账号密码 | 运行 Runner 前配置 |
| `DEVICE_UDID` | Android 设备 UDID | 运行 Runner 前配置 |

---

## 🔐 用户认证

系统实现了完整的用户认证机制：

- **用户注册**：用户名 + 密码，密码使用 bcrypt 加密存储
- **用户登录**：验证用户名和密码，返回 JWT Token（有效期 7 天）
- **Token 验证**：所有 API 请求通过 `Authorization: Bearer <token>` 头验证
- **数据隔离**：所有会话、消息、报告、用例、探索任务通过 user_id 关联，不同用户互不可见

---

## 📊 核心数据模型

| 表名 | 主要字段 | 说明 |
|------|----------|------|
| users | id, username, password_hash, created_at | 用户信息 |
| config | key, value | 平台配置 |
| sessions | id, user_id, name, knowledge_base_id | 会话信息 |
| messages | id, session_id, role, content | 聊天消息 |
| knowledge_bases | id, user_id, name, description | 项目知识库 |
| documents | id, kb_id, filename, file_path | 知识库文档 |
| api_interfaces | id, user_id, name, url, method, headers, params, body | 接口定义 |
| api_scenarios | id, user_id, name, description | 接口场景 |
| api_scene_steps | id, scenario_id, interface_id, sort_order | 场景步骤 |
| api_envs | id, user_id, name, variables | 接口测试环境 |
| api_test_results | id, user_id, interface_id, status_code, response | 接口执行结果 |
| web_cases | id, user_id, name, description, steps | Web 自动化用例（可执行步骤） |
| web_suites | id, user_id, name, case_ids | Web 用例集 |
| web_tasks | id, user_id, suite_id, task_type, cron_expression, status | Web 执行任务 |
| web_exploration_tasks | id, user_id, start_url, runner_task_id, status, summary | Web 探索任务 |
| app_devices | id, user_id, device_udid, app_package, app_activity, capabilities | Android 设备配置 |
| app_exploration_tasks | id, user_id, device_id, status, max_depth, max_actions, summary | App 探索任务 |
| app_test_runs | id, user_id, device_id, case_set_id, status, report_path, summary | Android 用例执行记录 |
| generated_case_sets | id, user_id, name, source_type, source_ref, case_count, metadata | 生成用例集 |
| generated_test_cases | id, user_id, set_id, title, module, priority, test_type, steps, expected | 生成的测试用例 |
| reports | id, session_id, user_id, test_type, url, status_code, title, screenshot, error | 测试报告 |

---

## 🔗 相关链接

- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [Streamlit 文档](https://docs.streamlit.io/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [Playwright 文档](https://playwright.dev/python/)
- [Appium 文档](https://appium.io/docs/en/latest/)

---

## 📌 注意事项

- API Key 请通过环境变量配置，不要硬编码到代码中。
- SQLite 数据库文件 `lyaitest.db` 会在首次启动时自动生成。
- 部署到 Render 时，需要在后台配置环境变量。
- JWT Secret Key 当前为默认值，生产环境请修改 `app/services/auth_service.py` 中的 `SECRET_KEY`。
- 录制与预登录必须有头浏览器，只能在本机桌面会话使用；无头环境请改用探索 + `steps` 手工编辑。
- 后端与 Runner 不同机时，务必配置 `LOCAL_RUNNER_TOKEN`，不要裸露 8765 端口。
