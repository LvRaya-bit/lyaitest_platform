# LYAITEST 本地 Runner

平台后端只做编排，真实执行全部下沉到这个本地 Runner。它同时承担两件事：

- **Web 自动化**：Playwright 驱动浏览器，负责屏幕录制（codegen）、自动探索（无头 BFS）、预登录（storageState）、用例执行
- **Android 自动化**：Appium + UiAutomator2 + Pytest，负责设备环境检测、登录、探索与用例执行

平台后端部署在本地或 Render 时，浏览器、Appium、ADB 和 Android 设备仍由本地 Runner 负责。
Render / Streamlit Cloud 无法访问开发电脑的 `127.0.0.1` 和本地模拟器。

## 架构边界

```text
Streamlit 前端 :8501
        │ HTTP + JWT
        ▼
FastAPI 后端 :8001（认证、编排、落库、轮询）
        │ LOCAL_RUNNER_URL
        ▼
本地 Runner :8765（本文件）
  ├─ Playwright：录制 / 预登录 / Web 探索 / Web 用例执行
  └─ Appium：设备检测 / APP 探索 / Pytest 用例执行
        │
        ▼
local_runner/jobs/<任务ID>/（JSON、DOM/XML、截图、storage_state、Trace）
```

Runner 不直接访问平台数据库，也不负责用例生成。用例生成在后端完成：先规则覆盖，
再由 AI 按模板逐条优化；Runner 只接收经过校验的可执行步骤并回传任务状态和产物。

## 安装

Web 自动化依赖（`requirements.txt` 已包含）：

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

Android 自动化依赖：

```powershell
pip install -r requirements-appium.txt
npm install -g appium
appium driver install uiautomator2
```

## 启动

只做 Web 自动化时，启动 Runner 即可：

```powershell
python local_runner/appium_runner.py --port 8765
```

需要 Android 自动化时，先另开一个终端启动 Appium：

```powershell
appium --port 4723
```

再启动 Runner：

```powershell
python local_runner/appium_runner.py --port 8765
```

Runner 监听地址可用 `--host` / `--port` 覆盖，也可通过 `RUNNER_HOST` / `RUNNER_PORT`
环境变量设置，默认 `127.0.0.1:8765`。

平台后端通过 `LOCAL_RUNNER_URL` 调用 Runner，默认值为 `http://127.0.0.1:8765`。
Web 用例、Web 探索、预登录在“Web自动化”页面维护；Android 设备配置、App 包名和
Activity 在“APP 自动化”页面维护。

## Runner 接口

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/jobs/{id}` | 查询任务状态与结果 |
| `DELETE` | `/jobs/{id}` | 删除已结束的任务与产物 |
| `POST` | `/preflight` | Android 设备环境检测（Appium / ADB / 设备 / App） |
| `POST` | `/explore` | 启动 Android 探索 |
| `POST` | `/run` | 执行 Pytest 用例 |
| `POST` | `/web-run` | 执行 Web 用例（Playwright） |
| `POST` | `/web-explore` | 启动 Web 探索（支持 `headless=true/false`） |
| `POST` | `/web-explore/{id}/stop` | 停止 Web 探索并保留已生成产物 |
| `POST` | `/web-login` | 打开有头浏览器预登录 |
| `POST` | `/web-login/{id}/save` | 保存 storageState |
| `POST` | `/web-login/{id}/cancel` | 取消预登录 |
| `POST` | `/record` | 用 `playwright codegen` 开始屏幕录制 |
| `GET` | `/record` | 查询当前未结束的录制 |
| `POST` | `/record/{id}/stop` | 强制终止录制（兜底，会丢失本次内容） |

所有接口都需要通过 `RUNNER_TOKEN` 校验（未配置令牌时跳过校验）。

运行产物统一保存在 `local_runner/jobs/<任务ID>/`，包含 Web 探索结果 JSON、DOM 快照、页面截图、
`storage_state.json`，Android 探索的 `exploration_result.json` / `*.xml` / `*.png`，以及执行失败时的
截图和 Playwright Trace。任务目录是结果回收和问题定位的事实来源。

## Web 自动化说明

### 屏幕录制

录制由 `playwright codegen` 完成，**必须有头运行**，只能在本机桌面会话里使用，无法放进
无人值守的 CI。结束方式是**关闭 codegen 打开的浏览器窗口**——只有窗口被正常关闭时
codegen 才会写出脚本，中途强制终止会拿不到录制内容。`POST /record/{id}/stop` 仅用于
浏览器窗口卡住时的兜底。

录制结束后，`record_converter.py` 用 AST 解析 codegen 脚本，产出平台统一的 `steps`
数组，并给出需要人工确认的 `warnings`。

### 预登录（storageState）

需要登录才能访问的站点，不必把账号密码写进脚本：

1. `POST /web-login` 打开有头浏览器到目标地址（任务状态为 `login_waiting`）；
2. 人工在浏览器里完成登录（含验证码）；
3. `POST /web-login/{id}/save` 写出 `storage_state.json`（cookie + localStorage）到任务目录；
4. 探索与执行时传入该 `storage_state` 路径，无头运行也带登录态。

未保存前可 `POST /web-login/{id}/cancel` 取消。

### 执行与探索

- 探索与执行都使用 `browser.new_context(ignore_https_errors=True)`，与录制侧行为保持一致，
  否则证书异常站点会整批失败。
- 探索默认只读：同源限制、危险词黑名单、表单提交拦截；开启“允许提交表单”后仅放行登录
  等提交动作，跨域链接与删除/支付类操作始终拦截。支持 `headless=true` 无头或 `false` 有头模式；
  运行中可调用 `/web-explore/{id}/stop` 停止，已生成产物会保留。上限为 `max_depth ≤ 8`、`max_actions ≤ 1000`。
- 用例步骤支持 4 种定位器：无前缀为 CSS、`role=X[name="Y"]`、`text=`、`testid=`。

## Windows 环境变量

平台页面中的“密码环境变量名”默认填写 `TEST_PASSWORD`，这里只填写变量名。
真实账号信息要在启动 Runner 的同一个终端窗口中设置。

Windows CMD：

```cmd
set "TEST_PHONE=你的手机号"
set "TEST_PASSWORD=你的密码"
set "DEVICE_UDID=你的设备UDID"
python local_runner\appium_runner.py --port 8765
```

PowerShell：

```powershell
$env:TEST_PHONE = "你的手机号"
$env:TEST_PASSWORD = "你的密码"
$env:DEVICE_UDID = "你的设备UDID"
python local_runner/appium_runner.py --port 8765
```

## Android 探索前自动登录

开始探索前，Runner 会识别登录页的手机号输入框、密码输入框、用户协议复选框和“开启不凡”按钮，并自动完成登录。账号信息只从 Runner 所在终端的环境变量读取，不会写入平台数据库：

- `TEST_PHONE`：测试手机号
- `TEST_PASSWORD`（或页面配置的密码环境变量名）：测试密码

如果没有配置账号，Runner 会保留登录页探索结果，并在任务结果中标记“未自动登录”，不会把登录页误判为已登录。

CMD 和 PowerShell 的环境变量语法不同，不要混用。

## 安全配置

如后端和 Runner 不在同一台电脑，双方配置同一个令牌：

```powershell
$env:RUNNER_TOKEN = "请替换为随机长字符串"
$env:LOCAL_RUNNER_TOKEN = $env:RUNNER_TOKEN
```

执行 Pytest 时，`RUNNER_ALLOWED_ROOTS` 用分号分隔允许的测试目录根路径。

> 不要直接对外暴露 8765 端口；跨机调度必须配置 `RUNNER_TOKEN`，并通过受保护的网络通道访问。
