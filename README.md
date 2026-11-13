v2.4.31：画面总结新增模型按需补看，可指定时间点和局部区域读取测评图表；默认 12 张预览、最多 48 张图片、3 轮补看，下载画质上限 1080p、时长上限 1 小时、文件上限 256 MiB。视频总结仍默认使用字幕，旧配置值继续保留。[设置与费用说明](docs/video-understanding.md)

v2.4.30：移除 Gemini 原生视频独立的接口地址、密钥及覆盖逻辑，直接复用所选 AstrBot Gemini 原生提供者的接口、认证、模型和代理配置。旧字段不再使用或回显；反代请统一在 AstrBot 提供者中配置。

v2.4.29：全面清理视频理解、酷安、知乎和动态摘要的手填模型名称及请求覆盖逻辑，各功能统一使用所选 AstrBot 提供者的模型配置；旧模型名称字段不再读取、回显或接受修改。如需使用其他模型，请在 AstrBot 中配置并选择对应提供者。

v2.4.28：移除重复的“指定内置模型标识”设置，默认模式跟随 AstrBot 当前默认模型，指定模型统一使用“AstrBot 指定平台”。`/总结模型` 命令同步修改该平台选择，重载后保持生效。旧版内置模型标识不再读取，原先使用该字段的用户请改在指定平台中选择。

v2.4.27：移除插件独立的自定义 OpenAI 兼容接口及地址、密钥、模型名称和温度配置，统一使用 AstrBot 当前默认模型或指定平台，保留备用模型切换。旧版自定义接口模式自动回到 AstrBot 默认模式，旧接口字段不再使用或回显；Gemini 原生视频专用配置保留。

v2.4.26：新增可选视频理解，支持字幕＋抽样画面、Gemini 原生视频及自动 / Static / Agentic。默认仍使用字幕总结，原生配置页和插件控制台可切换；渠道不支持时按设置回退并标明实际依据。[配置与使用说明](docs/video-understanding.md)

v2.4.25：原生配置页与插件控制台的固定选项、设置标题及提示说明尽量使用中文；保留实际模型标识和配置值，兼容旧版模板设置。备用模型继续支持多选与按顺序故障切换。

新增可选视频理解：保留字幕 / ASR 总结，支持“字幕＋抽样画面”和 Gemini 原生视频，以及自动 / Static / Agentic 处理模式。默认关闭视频理解，原生配置页或插件控制台均可开启；反代不支持时按设置回退并标注实际依据。[开启方式、实施清单与验证边界](docs/video-understanding.md)

Telegram 的视频总结、动态文字、订阅列表和视频订阅推送默认使用 Markdown
文字，跳过 QQ 图片排版、自动识别封面图和合并转发。动态中的原始图片仍作为媒体发送。
TG 视频总结保留段落，附带视频标题、UP 主、时长和原视频链接；QQ 的图片排版保留原行为。
NAS 联动平台会将搜索、获取、总结的过程提示更新到同一条消息。

v2.4.20：下载网页或元数据遇到 TLS EOF、连接重置时增加短暂重试；网络失败通知不再将总结标记为成功，随后按 5、15、60 分钟间隔最多补试三轮，期间不重复通知。每个 UP 优先处理最新视频，历史已标记失败需单独重做。同步修复 FFmpeg 定位缓存和失效平台订阅的重复处理。[下载恢复机制与边界](docs/download-recovery.md)

v2.4.19：修复正确转写因英文品牌识别差异、标题改写而触发“音频转写与视频标题不符”的问题。标题与正文中至少两个不同中文双字片段吻合时，也可作为相关性证据，兼容 `WorkBuddy` / `work body` 等真实转写差异；保留对无关内容的拦截。相关性校验仍为词语启发式，不能代替语义判断。

v2.4.18：增强中英混合标题的相关性检查，无关字幕会改用音频 ASR；内存/磁盘缓存都会检查正文，旧版总结缓存自动失效。同步上游的信息密度约束，并在提示词中要求简洁/专业/详细模式分别不超过 600/1200/1800 字，不逐句复述、不重复结论；用户配置了更小上限时取较小值。这是生成提示，实际篇幅仍取决于模型执行，`max_note_length` 继续作为生成后的截断限制。

v2.4.17：无可用字幕且必剪未识别出语音时，会明确说明原因，不再笼统报告“视频转写失败”。该旧版本依赖字幕或语音；现在可按上方说明另行开启画面或原生视频总结。

v2.4.16：QQ 总结图片会按实际像素安全分图，单边最多 28000、每张最多 6400 万像素；关闭章节分页时仍执行安全分图。合并转发会回读校验条目与图片是否齐全，订阅投递不完整时保留待推送。[QQ 图片边界实测记录](docs/qq-image-limits.md)

<div align="center">
  <img src="logo.png" alt="biliVideo" width="120">

# biliVideo Mod 魔改完美版 · AstrBot B 站/抖音 视频解析与 AI 总结

**丢一个 B 站/抖音 链接，AI 帮你秒出精华总结。**

[![AstrBot](https://img.shields.io/badge/AstrBot-v4.0%2B-blueviolet)](https://astrbot.app/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-v2.4.31-orange)](CHANGELOG.md)

</div>

---

## 🛠️ Mod 魔改定制版核心特性

本仓库由 **Astraea35** 维护并持续扩展，作为 **v1.0.0 独立魔改分支** 发版。基于底层现代微内核架构，深度缝合了以下硬核定制功能：

### 1. 🎨 渲染层：Playwright 线程隔离内核 + 字体自定义
- **彻底告别旧依赖**：移除了原版对系统级 `wkhtmltopdf` 软件的依赖，全面升级为现代化的无头浏览器 Playwright 内核。
- **异步循环冲突破解**：独创 `ThreadPoolExecutor` 线程隔离守护技术，将同步生图逻辑强行投递至独立的纯净子线程中运行，完美解决 Playwright 在 AstrBot 异步事件循环中碰撞报错（asyncio loop conflict）的业内痛点。
- **字体与自适应**：支持自定义 `.ttf` / `.otf` 本地字体路径（`custom_font_path`），留空自动回退系统默认无衬线字体。
- **双配色与 LaTeX**：白底绿字护眼模式与黑色极客深蓝分栏实时切换，注入 KaTeX 自动化公式动态解析。

### 2. 🔐 控制层：Token 防爆盾 (精细化自动总结拦截)
- **`all`**：全部环境直接触发自动总结。
- **`private_only`**：（推荐）仅在私聊机器人时才生成 AI 总结，群聊内粘贴链接只推送卡片简介，完美平衡群内互动与额度消耗。
- **`whitelist` / `blacklist`**：配合黑白名单列表，针对特定的群聊或用户发起绝对屏蔽或指定允许。

### 3. 🤖 模型层：AstrBot 平台原生双模桥接
- **免手填无缝对接**：直接调用 AstrBot 当前激活的默认模型。
- **`astrbot_custom` 模式**：直接在管理面板下拉菜单中指名调用已添加的第三方平台 ID（如 deepseek、openai 等）。
- **备用模型**：开启“启用故障备用重试”后，通过 AstrBot 原生多选模型选择器选择已配置的对话模型，并在已选列表中调整顺序；控制台也支持多选。主模型失败后依次尝试备用模型，成功后停止，重复模型仅尝试一次。每个备用模型使用其在 AstrBot 中的配置，不继承主模型名称；原有单个 `backup_provider_id` 会自动迁移为列表。修改后重载插件生效。

---

## 🎉 B站 / 抖音 完整生态支持

- ✅ **自动识别**：群里甩 B站/抖音 链接，自动弹出精美卡片（封面 + 作者 + 互动数据）。
- ✅ **自动总结**：支持字幕优先提取、语音 ASR 转写与智能总结。
- ✅ **合并转发**：卡片与 AI 总结打包成聊天记录，清爽不刷屏。

---

## 🚀 快速开始

```text
1. AstrBot 插件面板添加 Git 仓库地址一键安装：
   [https://github.com/Astraea35/astrbot_plugin_bilivideo_custom](https://github.com/Astraea35/astrbot_plugin_bilivideo_custom)
2. 重启 AstrBot
3. Send: /B站登录  → 扫码登录
4. Send: /总结 [https://www.bilibili.com/video/BV1xx411c7mD](https://www.bilibili.com/video/BV1xx411c7mD)

```

---

## 🖥️ WebUI 管理页面

插件重载后，可在 AstrBot 插件详情页打开 **BiliVideo 控制台 / dashboard** 页面：

- **平台接入**：Bilibili、YouTube、抖音分别使用独立区块，展示登录状态并提供凭据管理入口。
- **设置中心**：AI 模型、视频总结、图片渲染、自动识别、订阅推送、权限和搜索设置都可以在页面中保存。
- **页面外观**：支持上传自定义背景图，常见位图、SVG、AVIF、TIFF、ICO、JPEG2000 等格式会直接显示或自动转为 PNG。
- **安全处理**：API Key 和 Cookie 不会回显；SVG 上传会移除脚本与外部资源引用，背景图保存在插件数据目录中。

模型提供者、指定平台和备用模型等设置保存后建议重载插件；接口地址、密钥和生成参数统一在 AstrBot 中配置。普通开关和渲染选项会尽量即时应用。

---

## 🔧 完整指令大全

### 📌 基础总结与查询

| 命令 | 别名 | 功能说明 |
| --- | --- | --- |
| `/总结` | bv, BiliVideo, 视频总结 | 解析并总结 B站/YouTube/抖音 视频链接 |
| `/最新视频` | latest | 获取指定 UP 主最新视频并自动总结 |
| `/总结帮助` | bvhelp, 总结help | 显示帮助菜单与当前登录状态 |

### 🔐 登录与账号管理

| 命令 | 别名 | 功能说明 |
| --- | --- | --- |
| `/B站登录` | bvlogin, bili_login, 哔哩登录 | 生成二维码，扫码登录 B站 |
| `/B站登出` | bvlogout, bili_logout, 哔哩登出 | 清除 B站 Cookies |
| `/YT登录` | ytlogin, 油管登录 | 粘贴 Cookies 登录 YouTube（实验功能） |
| `/YT登出` | ytlogout, 油管登出 | 清除 YouTube Cookies |

### 📡 订阅与推送管理

视频订阅总结失败（包括内容不符、AI 超时）时，会等待 5、10、15 秒依次自动重试，最多额外重试 3 次。重试期间不发送失败消息；成功后正常推送总结，全部失败才发送一次带视频链接的错误通知。

如果最终失败属于网络断连或下载阶段超时，失败通知成功送达后会保留待恢复状态，再按 5、15、60 分钟间隔最多补试三轮；期间不重复通知，成功投递总结后才标记已完成。只跟踪每个 UP 的最新视频，新视频发布后优先处理新视频；旧版本已经记为已推送的历史失败不会自动重做。详见[下载恢复机制与边界](docs/download-recovery.md)。

| 命令 | 别名 | 功能说明 |
| --- | --- | --- |
| `/订阅` | sub, subscribe, 关注UP | 订阅 UP 主（支持 UID / 昵称 + 类型过滤） |
| `/取消订阅` | unsub, unsubscribe, 取关UP | 取消订阅（支持序号 / UID / 昵称） |
| `/订阅列表` | sublist, subs | 查看当前会话的所有订阅 |
| `/检查更新` | check, 手动检查 | 立即手动检查订阅 UP 主的新视频 |
| `/推送列表` | pushls, push_list | 管理员查看存在订阅的群聊和私聊 |
| `/移除推送 <序号>` | rmpush, remove_push | 管理员删除指定会话的全部订阅 |

### 🩺 系统状态与维护

| 命令 | 别名 | 功能说明 |
| --- | --- | --- |
| `/总结状态` | bvstat, 总结status | 查看版本、登录状态、LLM、渲染后端、缓存等 |
| `/总结清缓存` | bvclear, 清缓存 | 清除视频信息缓存、WBI密钥和总结结果缓存 |
| `/总结模型` | bvmodel, 切换模型 | 列出 / 切换 AstrBot 内置的 LLM 模型 |
| `/识别开关` | bvdetect, 切换识别 | 开启 / 关闭 B站/抖音 链接自动识别功能 |

---

## ⚙️ 常用配置项

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `output_image` | `true` | 总结以图片形式发送 |
| `custom_font_path` | `""` | 本地字体绝对路径（如 `/usr/share/fonts/SimHei.ttf`），留空使用系统字体 |
| `note_style` | `professional` | `concise` (简洁) / `detailed` (详细) / `professional` (结构化) |
| `auto_summary_mode` | `private_only` | 自动总结生效范围 (`all` / `private_only` / `whitelist` / `blacklist`) |
| `access_mode` | `all` | 插件总访问范围；手动和自动总结都必须先通过此范围 |
| `manual_summary_mode` | `all` | 手动总结范围；不能突破总访问范围 |
| `auto_summary_list` | `""` | 自动总结名单。订阅者不在自动总结范围内时，订阅会自动降级为仅推送提醒 |
| `enable_forward_message` | `false` | 总结合并转发（聊天记录形式） |
| `image_width` | `1320` | 图片画布宽度像素（适应大屏） |

---

## 🧪 开发与部署

```bash
git clone [https://github.com/Astraea35/astrbot_plugin_bilivideo_custom](https://github.com/Astraea35/astrbot_plugin_bilivideo_custom)
cd astrbot_plugin_bilivideo_custom

python -m pip install -r requirements.txt

```

---

## 📄 License

MIT © 2026 Astraea35 (Derived from original project by storyAura)

```

```
