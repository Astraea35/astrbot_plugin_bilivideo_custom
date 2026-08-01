<div align="center">
  <img src="logo.png" alt="biliVideo" width="120">

# biliVideo Mod 魔改完美版 · AstrBot B 站/抖音 视频解析与 AI 总结

**丢一个 B 站/抖音 链接，AI 帮你秒出精华总结。**

[![AstrBot](https://img.shields.io/badge/AstrBot-v4.0%2B-blueviolet)](https://astrbot.app/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-v1.0.0--mod-orange)](CHANGELOG.md)

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
