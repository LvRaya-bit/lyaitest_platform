"""平台级常量：Runner 提示文案等。

被多个页面模块共用，故从 app_frontend.py 抽出，避免页面文件反向依赖 app_frontend
导致循环 import。
"""

# 本机 Runner 不由平台托管，凡是 Runner 相关的提示都带上这条可直接复制的启动命令
_RUNNER_START_COMMAND = r"venv\Scripts\python.exe local_runner\appium_runner.py --port 8765"
_RUNNER_HINT = f"请在项目根目录启动本机 Runner：`{_RUNNER_START_COMMAND}`"

# Web 用例执行轮询参数：被 web_automation 与 case_generation（批量执行）共用，故放公共层
_WEB_RUN_POLL_INTERVAL = 2
_WEB_RUN_POLL_TIMEOUT = 900
_WEB_RUN_MAX_LOG_CHARS = 4000
