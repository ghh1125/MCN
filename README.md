# MCN Workflow

一个以 Streamlit 网页交互为中心的 MCN 内容生产工作流。
当前产物是“文本创作剧本包 + 本地保存的视频文件”。

## 快速开始

1. 安装依赖

```bash
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -e .
```

2. 配置环境变量

直接编辑项目根目录下的 `.env`。

注意：

- `.env` 里这些值默认留空，实际以网页当前会话里的选择为准
- `3 个 API Key` 不再写进 `.env`
- API Key、搜索 provider / URL、规划模型 URL / 模型、视频提交 URL / 模型现在都可以在网页左侧边栏按会话覆盖

其中最关键的是这些配置：

- 前半段策划/分析/脚本生成：`PLANNING_BASE_URL`、`PLANNING_MODEL`
- 联网搜索：`SEARCH_API_PROVIDER`、`SEARCH_API_URL`
  当前前端默认提供 `tikhub` 和 `自定义` 两种方式
  其中 `SEARCH_API_URL` 对 `tikhub` 来说应填写基地址，例如 `https://api.tikhub.io`
  小红书默认会用 `SEARCH_XIAOHONGSHU_CONTENT_MODE=search_notes`，也就是直接走笔记搜索接口；如果账号没有该接口权限，网页会给出平台级错误提示，但不会影响其他已勾选平台
  如果要保存 TikHub 原始返回用于联调，可开启 `SEARCH_DEBUG_SAVE_RAW=true`
- 后半段视频生成：`ENABLE_VIDEO_PIPELINE`、`VIDEO_API_URL`
  当前默认已兼容 DashScope 文生视频，额外可配：
  `VIDEO_API_PROVIDER=dashscope`、`VIDEO_MODEL`、`VIDEO_MODE`、`VIDEO_ASPECT_RATIO`、`VIDEO_DURATION_SECONDS`、`VIDEO_AUDIO`、`VIDEO_WATERMARK`
- 本地保存视频：`SAVE_VIDEO_TO_DISK`、`VIDEO_OUTPUT_DIR`
- 可选自动发布：`ENABLE_PUBLISH_PIPELINE`、`PUBLISH_API_URL`、`PUBLISH_API_KEY`

3. 启动网页工作台

```bash
source .venv/bin/activate
streamlit run streamlit_app.py
```

启动后在网页侧边栏输入这 3 个 key，并按需选择 URL / 模型预设：

- 策划大模型 API Key
- 搜索 API Key
- 视频 API Key
- 搜索 Provider / URL
- 规划模型 URL / 模型
- 视频提交 URL / 模型

页面主区域再输入创作需求、达人 ID 和平台。
当前默认推荐先选 `douyin`，因为这条链路的 TikHub 站内搜索和热榜接口最完整。
现在网页里已经拆成两部分：
- `搜索平台（可多选）`：决定从哪些平台抓参考内容和热点
- `目标发布平台 / 封面投放平台`：决定后续剧本、封面文案和视频风格更偏向哪个平台

## 项目环境

项目专用 Python 虚拟环境已经放在 `.venv/`。

常用命令：

```bash
cd /Users/ghh/Documents/Code/mcpify/MCN
source .venv/bin/activate
python --version
```

## 说明

- 检索节点通过外部搜索 API 联网获取参考内容和热点信息
- `SEARCH_API_PROVIDER=tikhub` 时，会按平台走专用 adapter：
  小红书默认 `app_v2/search_notes`，抖音 `search/fetch_general_search_v1`，B站 `web/fetch_general_search`
- 当前最推荐的实跑平台是 `douyin`；小红书更适合做笔记场景补充，不建议作为这版默认发现入口
- 这版默认只走“搜索类 API”；页面里展示的“热点”目前是从搜索结果标题里提炼出来的主题线索，不再额外调用热榜接口
- 加 `--debug-search` 或设置 `SEARCH_DEBUG_SAVE_RAW=true` 时，会把 TikHub 原始响应保存到 `SEARCH_DEBUG_OUTPUT_DIR`
- 网页端左侧边栏会要求输入 3 个 API Key；这些 key 只在当前会话生效，不会写回 `.env`
- 默认流程会输出 `creative_script_text`、`shot_outline`、`text_to_video_prompt`
- 当前剧本结构已细化为“创作蓝图”形式，额外包含 `roles`、`creative_goals`、`core_conflict`、`story_beats` 等字段，方便做多角色、多目标和更细粒度的改稿
- 主题确认后，用户需要先确定目标视频秒数，再生成对应长度的剧本
- 如果视频生成因为秒数问题失败，页面会引导先修改秒数、重写剧本、再重新审核后生成视频
- 默认流程会在剧本生成后继续调用视频 API，并把视频下载到 `VIDEO_OUTPUT_DIR`
- DashScope 这类已知异步服务会自动发送 `X-DashScope-Async: enable`，并自动推导状态查询接口
- 只有把 `ENABLE_PUBLISH_PIPELINE=true`，流程才会继续调用发布节点
- 当前默认是分步骤交互流程：输入需求 -> 检索与出题 -> 主题确认 -> 剧本确认 -> 生成视频
- 如果不同意主题或剧本，网页会要求输入原因，并带着这条反馈重新检索或重写
