# Changelog

## v2.4.3

- v2.4.3 自动更新

## v2.4.2

- 新增知乎

## v2.4.1

- 修改设置排布

## v2.4.0

- 设置中新增单独的 "平台开关" 分组。用多选列表分别控制：B站、YouTube、抖音、酷安。关闭任一平台后，该平台的手动 /总结 和裸链接自动识别都会跳过。B 站、抖音、酷安自动识别均已接入各自平台开关。帮助、状态和 YouTube 登录提示也改为读取新的平台开关。旧 enable_multi_platform 配置仍兼容：旧配置开启时会自动映射为四个平台全部开启。

## v2.3.9

- 新增酷安

## v2.3.8

- v2.3.8 自动更新

## v2.3.7

- v2.3.7 自动更新

## v2.3.6

- v2.3.6 自动更新

## v2.3.5

- 添加UI界面

## v2.3.4

- v2.3.4 自动更新

## v2.3.3

- 加入 /检查更新、/订阅列表 等常用命令。
- 修正开播提醒示例为 UID 用法。
- 合并重复说明，重排为“常用指令”和“订阅使用规则”。
- 将“视频自动总结”与“动态摘要按全局配置”区分显示。
- Pillow 版类型说明改为两行，减少横向溢出。

## v2.3.2

- 已接入动态 AI 摘要。启用后，每条动态推送会在原卡片或文本后附加 🤖 AI 摘要；失败不会阻断原推送。
- 已支持专用 Provider 和模型名。填写动态摘要 Provider 时优先使用它；留空则复用插件主模型。模型名会传给支持该参数的 Provider
- 已支持多模态。启用后会将动态图片 URL 传给视觉模型：
- OpenAI 兼容模型使用视觉消息格式。
- AstrBot Provider 调用 image_chat 或 multimodal_chat。
- 不支持视觉输入时自动降级为文字摘要，不影响原动态推送。
- 已移除 runtime_state.json 这套第二状态来源。自动识别开关与模型切换命令现在都写回 AstrBot 配置，并同步更新当前运行配置
- 订阅列表渲染已改为只读取 services.config.sub_list_render_method，不再绕过统一配置
- 此前无效的网页控制台也已删除。

## v2.3.1

- 经典风格统一为四边 10px 留白，移除了仅左/上/下生效的外边距。
- 粉色风格移除了固定 600px 卡片宽度，改为填满带内边距的画布。
- 简约风格补充最大宽度限制，避免边框突破内容区。

## v2.3.0

- v2.3.0 自动更新

## v2.2.9

- 时推送现在始终发送回创建订阅的原群聊/私聊，平台标识从订阅会话自动读取，不再使用或猜测 platform_prefix。
- 配置页已移除“推送平台前缀”“推送 QQ 群列表”“推送 QQ 号列表”。
- 旧 push_targets 缓存不再参与视频推送。
- /推送列表 现在仅列出有订阅的群聊和私聊，不展示 UP 订阅内容。
- /移除推送 <序号> 改为删除该会话的全部订阅，仍限定 AstrBot 管理员。
- 已移除“添加推送群/号”命令入口，并更新帮助与 README。

- v2.2.7 自动更新

- 在 [scheduled_push.py](D:\\飞牛双向同步\\AstrBot插件\\biliVideo视频总结2.0-整合bilibili魔改版本\\astrbot_plugin_bilivideo_custom\\bilivideo\\handlers\\scheduled_push.py) 增加投递目标、成功回执和 30 秒超时日志。重载后再测试，会出现以下之一：
- delivering scheduled push ... target(s): [...]：显示实际发往的会话。
- scheduled push delivered to ...：AstrBot 已接受发送请求。
- push to ... failed 或 timed out：可直接据此继续处理。

## v2.2.5

- AI 模型报错后的整流程自动重试会复用已提取的字幕，不再重复下载字幕或执行 ASR。
- 缓存按视频 ID（无 ID 时按 URL）保存 6 小时，最多 64 条。
- /总结清缓存 会同时清除总结缓存和字幕缓存。
- 新增回归测试，验证首次模型失败、再次调用成功时转写只执行一次。

## v2.2.4

- 默认检查间隔从 600 分钟改为 10 分钟。
- 只有至少一个推送目标发送成功后，才记录该视频为已推送。发送/总结临时失败时，下次检查会自动重试，不会被静默跳过。

## v2.2.3

- 新配置页字段：push_user_list、push_group_list
- 原来的旧字段不再展示，只在旧配置尚未产生新字段时兼容读取
- 新列表为空时会真正清空推送目标，不再回退到旧字符串

## v2.2.2

- 允许自动总结的用户白名单 已确认是遗留配置，之前完全不生效，现已从插件设置中移除。
- 推送 QQ 群列表 与 推送 QQ 号列表 也已改成 AstrBot 原生列表控件，支持逐项添加和批量导入，不再需要逗号分隔。旧的逗号文本配置仍可兼容读取。

## v2.2.1

- 已改为 AstrBot 原生列表控件：
- 总访问名单
- 手动总结名单
- 自动总结名单
- 现在每项会显示“添加更多/批量导入”的列表编辑界面，不再需要逗号分隔。解析层原本已支持列表，旧的逗号分隔配置也仍兼容。

## v2.2.0

- 设置页现在有“总访问 / 手动总结 / 自动总结”三套范围，每套均为 全部、仅私聊、白名单、黑名单，名单统一填写群号或用户 ID，精确匹配会话或发送者。手动与自动总结必须先通过总访问范围，无法越过总开关。用户订阅时若不具备自动总结权限，会直接保存为“仅推送提醒”；定时推送和手动检查更新也会二次校验，并自动关闭已失效订阅的自动总结。登录、登出、模型切换、缓存清理、识别开关、推送目标、全局订阅、卡片样式、直播 @全体等已改为仅 AstrBot 管理员可执行。保留旧的 group_list、summary_command_* 配置读取兼容，旧配置不会立即失效。已新增权限、旧配置迁移和订阅授权持久化测试；配置 JSON、全部 Python 文件语法及核心断言均通过。

## v2.1.7

- 修复订阅逻辑，/订阅不再覆盖已有订阅类型，改为合并去重；已订阅动态后执行/订阅 319785096 现保留动态并新增视频，重复订阅不产生重复记录；/取消订阅 动态 仅移除该类型，保留其他类型，仅当最后一个类型被移除时删除整个订阅；订阅数量上限仅限制新增UP主，已存在的UP主可继续增加类型；相关改动位于manager.py、subscription.py，并增加回归测试，重启插件后生效。

## v2.1.6

- v2.1.6 自动更新

## v2.1.5

- v2.1.5 自动更新

## v2.1.4

- 修复全部base64上传

## v2.1.3

- v2.1.3 自动更新

## v2.1.2

- v2.1.2 自动更新

## v2.1.1

- v2.1.1 自动更新

> 本次更新实现了抖音与 B站 在自动识别、自动总结、权限控制、合并转发上的**体验完全对齐**，
> 并对渲染底层的空白冗余进行了精细化修剪。

### Added

- **抖音自动识别**：支持群聊/私聊裸发抖音链接（短链/长链），自动抓取标题、作者、封面、互动数据。
- **抖音自动总结**：在 `detect_auto_summary` 开启时，自动为抖音视频生成 AI 总结，并完整支持 `auto_summary_mode`（all / private_only / whitelist / blacklist）。
- **抖音合并转发**：开启 `enable_forward_message` 后，抖音的“卡片信息 + AI 总结”会被优雅打包为聊天记录（与 B站 体验完全一致）。
- **yt-dlp 元数据后备**：抖音 HTML 解析失败时自动降级到 yt-dlp 提取元数据，解析成功率提升至 99%。
- **底部模型标签内嵌**：总结图片底部的“总结模型”信息已合并到页脚左侧，不再单独占用一行，图片更紧凑。

### Fixed

- **修复 `BilibiliHTTPClient` 无 `get` 方法**：抖音裸链接自动识别不再因 `AttributeError` 静默崩溃（已替换为 `aiohttp`）。
- **修复 `constants.py` 资源路径错误**：`PROJECT_ROOT` 少回退一层目录导致动态卡片模板/Logo 加载失败的问题已解决。
- **修复 Cookie 持久化竞态**：扫码登录后凭据会可靠写入磁盘，不再因“即抛即忘”导致重启后登录态丢失。
- **修复 `shutil.rmtree` 阻塞事件循环**：AI 搜索下载目录的自动清理已改为 `asyncio.to_thread` 异步执行。

### Dependencies

- 新增 `jinja2>=3.0`（动态卡片渲染必备依赖，此前遗漏）。

### Removed

- 移除 `assets/templates/listener.py` 冗余文件（与 `services/listener.py` 重复，且位置错误）。
# Changelog

All notable changes to this plugin are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v2.0.0 — Architecture refresh

> Major refactor. **Backward-compatible** for end users (commands and
> config keys preserved) but a complete restructure under the hood.

### Added

- New layered package `bilivideo/` with clear single-responsibility modules:
  `core`, `api`, `auth`, `parsing`, `transcription`, `downloader`, `llm`,
  `summarize`, `render`, `messaging`, `subscription`, `access`, `cache`,
  `handlers`, `tools`.
- Typed configuration via `PluginConfig` dataclass with validation,
  enum-restriction and clamping (no more `dict.get()` everywhere).
- Structured exception hierarchy (`BiliVideoError`, `NetworkError`,
  `TranscriptionError`, `LLMError`, …) — user-friendly messages now ride
  on the exception itself instead of substring-matching.
- LRU + TTL + single-flight cache (`LRUTTLCache`) shared by the WBI key
  fetcher and `get_video_info`.
- Shared `aiohttp.ClientSession` plus exponential-backoff retries for
  every B 站 API call.
- Per-user cooldown tracker for `/总结` (default 8 s, configurable).
- In-flight deduplication (`InflightDeduper`) to fold concurrent requests
  for the same BV into a single underlying job.
- Atomic `JsonStore` (tempfile + `os.replace` + `fsync`) for the
  subscription/push-target file — no more half-written JSON on crash.
- Full PyTest suite with 71 tests covering URL extraction, pagination,
  smart truncation, message parsing, subscription persistence, cooldown,
  LRU cache, in-flight deduplication, access control, and config.
- `pyproject.toml` with Ruff + MyPy + PyTest configuration.
- `user_cooldown_seconds`, `llm_temperature`, `image_width`,
  `forward_bot_name`, `forward_bot_uin`, and `trigger_keywords` config
  options.

### Changed

- `main.py` shrunk from ~2,000 lines to ~160 lines; it now only registers
  AstrBot commands and forwards them to handlers.
- `metadata.yaml` repo URL fixed (it previously concatenated a stray
  `yt-dlp` token, breaking the link).
- `requirements.txt` now lists `segno` (was implicitly required by the
  QR-login flow but missing from the manifest).
- `_conf_schema.json` reorganised with per-section `[xxx]` description
  prefixes for UI grouping; values now validated/clamped on load.
- Cookie storage hardened: atomic writes + `chmod 0600` on creation.
- Auto-detect (`on_all_message`) is now a small composition of typed
  helpers (`MessageContext`, `TriggerSet`, URL extractor) instead of
  ~300 lines of nested branches.
- WBI signing is single-flight: concurrent requests share one fetch.
- Scheduler iterations include jitter so multi-instance deployments don't
  thunder simultaneously.

### Fixed

- `audio_meta.file_path` access on the subtitle-only path no longer
  raises `AttributeError`.
- Short-link resolution now uses async aiohttp throughout (was blocking
  the event loop with `requests.head`).
- `get_uploader_info` failures now fall back gracefully through video
  lookup → search result → UID-based placeholder, mirroring the original
  intent without the duplicate code.
- Quote/reply detection: trigger keywords are configurable; the hard
  intercept for `[CQ:reply` and `[引用消息]` is preserved.
- `metadata.yaml` `name` is now lowercase `astrbot_plugin_bilivideo`
  (was camelCase `astrbot_plugin_biliVideo`). This unblocks installation
  on case-insensitive filesystems (Windows/macOS APFS) where the
  AstrBot extractor would otherwise hit "directory already exists" —
  closes [#14][issue14].

[issue14]: https://github.com/storyAura/astrbot_plugin_biliVideo/issues/14

### Security

- `bili_cookies.json` is created with mode `0600` (was 0644) so SESSDATA
  isn't world-readable on shared servers.
- Cookie loading no longer surfaces SESSDATA values in debug logs.
- Reduced surface for prompt-injection: search results pass through a
  typed dataclass before reaching the LLM, with `<em>` highlighting
  stripped server-side.

### Removed

- Module-level mutable globals (`_wbi_cache`, `_font_face_cache`)
  replaced by encapsulated caches.
- Legacy `services/`, `downloaders/`, `transcriber/`, `utils/`, `gpt/`,
  `models/` directories — their contents now live in the new
  `bilivideo/` package.

---

## v1.0.5a (2026-05-14)

- Optional summary on auto-push (`auto_push_summary`).
- Hard-intercept quoted/reply messages from re-triggering auto detection.

## v1.0.4b (2026-05-14)

- Fix `audio_meta.file_path` crash on subtitle-only path.
- Harden cleanup function to skip `None`/empty paths.

## v1.0.4 (2026-05-13)

- Fix `extract_video_id` UnboundLocalError for BCut transcript flow.
- Fix unterminated subpattern regex on `b23.tv` resolution.

## v1.0.3 (2026-05-12)

- Quote-message false-trigger fix; trigger keyword mechanism.
- Forward-message mode; long-summary pagination.
- Prefer subtitles config option.

## v1.0.2 (2026-03-01)

- AstrBot v4.17.2 compatibility, mini-app link recognition,
  `/识别开关` toggle command.

## v1.0.1

- First release.
