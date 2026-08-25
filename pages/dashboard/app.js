const bridge = window.AstrBotPluginPage;
const state = { context: null, data: null, localTheme: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const fieldGroups = {
  aiMain: [
    { key: "llm_provider", label: "主模型提供者", type: "select", options: [["astrbot", "AstrBot 当前模型"], ["astrbot_custom", "AstrBot 指定平台"], ["openai_compatible", "OpenAI 兼容 API"]] },
    { key: "astrbot_provider_id", label: "AstrBot 平台 ID", hint: "例如 deepseek、openai", type: "text" },
    { key: "llm_provider_id", label: "内置模型 ID", hint: "留空使用当前默认模型", type: "text" },
    { key: "llm_api_base", label: "API Base URL", type: "url" },
    { key: "llm_api_key", label: "API Key", hint: "留空保持当前密钥", type: "password", secret: true },
    { key: "llm_model", label: "模型名称", type: "text" },
    { key: "llm_temperature", label: "Temperature", type: "number", step: "0.1", min: "0", max: "2" },
    { key: "enable_fallback", label: "启用故障备用重试", hint: "主模型失败时自动调用备用模型", type: "switch" },
    { key: "backup_provider_id", label: "备用平台 ID", type: "text" },
    { key: "bangumi_token", label: "Bangumi Access Token", hint: "可选，留空保持当前令牌", type: "password", secret: true },
  ],
  aiDynamic: [
    { key: "enable_dynamic_ai_summary", label: "动态 AI 摘要", hint: "为订阅动态额外生成摘要", type: "switch" },
    { key: "dynamic_summary_provider", label: "动态摘要 Provider ID", type: "text" },
    { key: "dynamic_summary_model", label: "动态摘要模型", hint: "留空使用默认模型", type: "text" },
    { key: "enable_multimodal_dynamic_summary", label: "多模态动态摘要", hint: "将动态图片交给视觉模型", type: "switch" },
  ],
  summary: [
    { key: "note_style", label: "总结风格", type: "select", options: [["concise", "简洁要点"], ["detailed", "完整记录"], ["professional", "结构化分析"]] },
    { key: "enable_link", label: "插入时间戳", hint: "在总结中嵌入跳转标记", type: "switch" },
    { key: "enable_summary", label: "末尾 AI 总结段落", type: "switch" },
    { key: "max_note_length", label: "最大总结长度", type: "number", min: "500", max: "60000" },
    { key: "prefer_subtitle", label: "优先使用平台字幕", type: "switch" },
    { key: "subtitle_langs", label: "字幕语言优先级", hint: "使用逗号分隔，例如 zh-Hans,zh,en", type: "text", list: true },
    { key: "download_quality", label: "下载质量", type: "select", options: [["fast", "快速"], ["medium", "均衡"], ["slow", "高质量"]] },
  ],
  render: [
    { key: "output_image", label: "输出总结图片", type: "switch" },
    { key: "theme", label: "卡片主题", type: "select", options: [["light", "浅色"], ["dark", "深色"]] },
    { key: "renderer_template", label: "渲染模板", type: "select", options: [["template_1", "经典风格"], ["template_2", "B站粉风格"], ["simple", "简约风格"]] },
    { key: "custom_font_path", label: "自定义字体路径", hint: "可选，填写服务器上的字体文件路径", type: "text", wide: true },
    { key: "image_scale_factor", label: "图片缩放倍数", type: "select", options: [["1", "1x"], ["2", "2x"], ["3", "3x"], ["4", "4x"]] },
    { key: "image_width", label: "图片宽度", type: "number", min: "800", max: "2400" },
    { key: "image_output_format", label: "图片格式", type: "select", options: [["png", "PNG"], ["jpg", "JPG"], ["webp", "WebP"]] },
    { key: "image_quality", label: "图片质量", type: "number", min: "1", max: "100" },
    { key: "enable_auto_split", label: "自动拆分长图", type: "switch" },
    { key: "max_cards_per_image", label: "每张图片最大卡片数", type: "number", min: "2", max: "12" },
    { key: "image_font_size", label: "图片字体大小", type: "number", min: "16", max: "64" },
  ],
  general: [
    { key: "debug_mode", label: "调试模式", hint: "开启 DEBUG 级别日志", type: "switch" },
    { key: "processing_timeout", label: "单次总结超时（秒）", type: "number", min: "60", max: "1800" },
    { key: "user_cooldown_seconds", label: "用户冷却（秒）", hint: "设为 0 关闭", type: "number", min: "0", max: "600" },
    { key: "interval_secs", label: "UP 主动态检测周期（秒）", type: "number", min: "10", max: "3600" },
    { key: "task_gap_secs", label: "相邻 UP 主任务间隔（秒）", type: "number", min: "1", max: "120" },
    { key: "reconnect_silent", label: "重连静默模式", type: "switch" },
    { key: "reconnect_silent_threshold_secs", label: "静默触发阈值（秒）", type: "number", min: "60", max: "86400" },
    { key: "recent_dynamic_cache", label: "动态缓存大小", type: "number", min: "1", max: "20" },
    { key: "dynamic_limit", label: "单次推送动态上限", type: "number", min: "1", max: "20" },
  ],
  detect: [
    { key: "enable_miniapp_detect", label: "启用自动识别", hint: "识别聊天中的视频链接", type: "switch" },
    { key: "detect_show_cover", label: "展示封面", type: "switch" },
    { key: "detect_show_uploader", label: "展示作者", type: "switch" },
    { key: "detect_show_desc", label: "展示简介", type: "switch" },
    { key: "detect_show_pubtime", label: "展示发布时间", type: "switch" },
    { key: "detect_show_link", label: "展示原链接", type: "switch" },
    { key: "detect_show_stats", label: "展示互动数据", type: "switch" },
    { key: "detect_auto_summary", label: "识别后自动总结", type: "switch" },
    { key: "trigger_keywords", label: "触发关键词", hint: "使用逗号分隔", type: "textarea", list: true, wide: true },
  ],
  subscription: [
    { key: "enable_auto_push", label: "启用自动推送", type: "switch" },
    { key: "auto_push_summary", label: "推送时生成总结", type: "switch" },
    { key: "check_interval_minutes", label: "检查间隔（分钟）", type: "number", min: "5", max: "1440" },
    { key: "max_subscriptions", label: "每会话最大订阅数", type: "number", min: "1", max: "100" },
    { key: "sub_list_render_method", label: "订阅列表渲染", type: "select", options: [["direct", "本地渲染"], ["browser", "浏览器渲染"]] },
    { key: "plain_push_template", label: "纯文本推送模板", hint: "支持 {name} {uid} {title} {text} {url}", type: "textarea", wide: true },
    { key: "plain_push_forward_template", label: "纯文本转发模板", type: "textarea", wide: true },
    { key: "ai_summary_prompt", label: "动态 AI 摘要提示词", hint: "支持 {content}", type: "textarea", wide: true },
  ],
  message: [
    { key: "enable_forward_message", label: "启用合并转发", hint: "将卡片与总结打包为聊天记录", type: "switch" },
    { key: "forward_bot_name", label: "转发机器人名称", type: "text" },
    { key: "forward_bot_uin", label: "转发机器人 QQ 号", type: "text" },
  ],
  accessSearch: [
    { key: "access_mode", label: "总访问范围", type: "select", options: [["all", "全部允许"], ["private_only", "仅私聊"], ["whitelist", "白名单"], ["blacklist", "黑名单"]] },
    { key: "access_list", label: "总访问名单", hint: "群号或用户 ID，逗号分隔", type: "text", list: true },
    { key: "manual_summary_mode", label: "手动总结范围", type: "select", options: [["all", "全部允许"], ["private_only", "仅私聊"], ["whitelist", "白名单"], ["blacklist", "黑名单"]] },
    { key: "manual_summary_list", label: "手动总结名单", type: "text", list: true },
    { key: "auto_summary_mode", label: "自动总结范围", type: "select", options: [["all", "全部允许"], ["private_only", "仅私聊"], ["whitelist", "白名单"], ["blacklist", "黑名单"]] },
    { key: "auto_summary_list", label: "自动总结名单", type: "text", list: true },
    { key: "default_count", label: "默认搜索返回数", type: "number", min: "1", max: "50" },
    { key: "default_download_count", label: "建议每次下载数", type: "number", min: "1", max: "20" },
    { key: "search_max_concurrent", label: "搜索下载并发数", type: "number", min: "1", max: "5" },
    { key: "search_show_progress", label: "显示下载进度", type: "switch" },
  ],
};

function renderField(def) {
  const fieldClass = `field${def.wide ? " wide" : ""}`;
  const hint = def.hint ? `<span class="field-hint">${def.hint}</span>` : "";
  const id = `field-${def.key}`;
  if (def.type === "switch") {
    return `<label class="${fieldClass}"><span class="field-label"><span>${def.label}</span>${hint}</span><span class="switch-field"><span>启用</span><span class="switch"><input id="${id}" data-config="${def.key}" data-kind="switch" type="checkbox" /><i class="switch-ui"></i></span></span></label>`;
  }
  let control = "";
  if (def.type === "select") {
    control = `<select id="${id}" data-config="${def.key}" data-kind="select">${def.options.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select>`;
  } else if (def.type === "textarea") {
    control = `<textarea id="${id}" data-config="${def.key}" data-kind="textarea" ${def.list ? "data-list=\"true\"" : ""}></textarea>`;
  } else {
    const secret = def.secret ? "autocomplete=\"new-password\"" : "";
    control = `<input id="${id}" data-config="${def.key}" data-kind="${def.type}" ${def.list ? "data-list=\"true\"" : ""} type="${def.type === "password" ? "password" : def.type}" ${def.step ? `step="${def.step}"` : ""} ${def.min ? `min="${def.min}"` : ""} ${def.max ? `max="${def.max}"` : ""} ${secret} />`;
  }
  return `<label class="${fieldClass}"><span class="field-label"><span>${def.label}</span>${hint}</span>${control}</label>`;
}

function renderFields(containerId, defs) {
  const container = document.getElementById(containerId);
  if (container) container.innerHTML = defs.map(renderField).join("");
}

function renderAllFields() {
  renderFields("ai-main-fields", fieldGroups.aiMain);
  renderFields("ai-dynamic-fields", fieldGroups.aiDynamic);
  renderFields("summary-fields", fieldGroups.summary);
  renderFields("render-fields", fieldGroups.render);
  renderFields("general-fields", fieldGroups.general);
  renderFields("detect-fields", fieldGroups.detect);
  renderFields("subscription-fields", fieldGroups.subscription);
  renderFields("message-fields", fieldGroups.message);
  renderFields("access-search-fields", fieldGroups.accessSearch);
}

function statusPill(label, kind = "neutral") { return `<span class="status-pill ${kind}">${label}</span>`; }

function platformCard(platform, compact = false) {
  const p = state.data?.status || {};
  const info = {
    bilibili: { name: "Bilibili", sub: "B站视频与动态", logo: "BV", cls: "bili", ok: p.bilibili_logged_in, label: p.bilibili_logged_in ? "已登录" : "未登录" },
    youtube: { name: "YouTube", sub: "YouTube 视频解析", logo: "YT", cls: "youtube", ok: p.youtube_cookies, label: p.youtube_cookies ? "Cookies 已保存" : "未配置" },
    douyin: { name: "抖音", sub: "短链与长链识别", logo: "抖", cls: "douyin", ok: p.multi_platform, label: p.multi_platform ? "已启用" : "未启用" },
  }[platform];
  const status = statusPill(info.label, info.ok ? "success" : "warning");
  if (compact) return `<article class="platform-card compact-card"><div class="platform-card-head"><div class="platform-brand"><span class="platform-logo ${info.cls}">${info.logo}</span><div><h3>${info.name}</h3><p>${info.sub}</p></div></div>${status}</div></article>`;
  if (platform === "bilibili") {
    return `<article class="platform-card"><div class="platform-card-head"><div class="platform-brand"><span class="platform-logo bili">BV</span><div><h3>Bilibili</h3><p>视频、动态、订阅与 Bangumi</p></div></div><span id="bili-status">${status}</span></div><p class="platform-description">可使用聊天命令扫码登录，也可以在这里粘贴已有 Cookie。</p><div class="cookie-grid"><input id="bili-sessdata" type="password" placeholder="SESSDATA（必填）" /><input id="bili-jct" type="password" placeholder="bili_jct" /><input id="bili-uid" type="text" placeholder="DedeUserID" /><input id="bili-uid-md5" type="text" placeholder="DedeUserID__ckMd5" /></div><div class="cookie-note">当前已保存字段：<strong id="bili-cookie-keys">暂无</strong></div><div class="platform-actions"><button id="save-bili" class="primary-button" type="button">保存 B站凭据</button><button id="logout-bili" class="secondary-button" type="button">退出登录</button></div></article>`;
  }
  if (platform === "youtube") {
    return `<article class="platform-card"><div class="platform-card-head"><div class="platform-brand"><span class="platform-logo youtube">YT</span><div><h3>YouTube</h3><p>yt-dlp Cookies 登录</p></div></div><span id="youtube-status">${status}</span></div><p class="platform-description">粘贴浏览器导出的 Netscape cookies.txt，或直接粘贴 Cookie 请求头。</p><textarea id="youtube-cookies" class="cookie-textarea" placeholder="# Netscape HTTP Cookie File\n或 name=value; name2=value2"></textarea><div class="platform-actions"><button id="save-youtube" class="primary-button" type="button">保存 YouTube Cookies</button><button id="logout-youtube" class="secondary-button" type="button">清除</button></div></article>`;
  }
  return `<article class="platform-card"><div class="platform-card-head"><div class="platform-brand"><span class="platform-logo douyin">抖</span><div><h3>抖音</h3><p>短链 / 长链自动识别</p></div></div><span id="douyin-status">${status}</span></div><p class="platform-description">抖音不需要额外账号凭据。打开实验性多平台开关后即可启用识别、元数据卡片和自动总结。</p><label class="field"><span class="field-label"><span>启用抖音 / YouTube 多平台</span><span class="field-hint">实验功能</span></span><span class="switch-field"><span>启用</span><span class="switch"><input id="field-enable_multi_platform" data-config="enable_multi_platform" data-kind="switch" type="checkbox" /><i class="switch-ui"></i></span></span></label><div class="cookie-note">支持抖音链接识别、封面、作者、简介、互动数据和可选 AI 总结。</div></article>`;
}

function renderPlatforms() {
  $("#platform-grid").innerHTML = [platformCard("bilibili"), platformCard("youtube"), platformCard("douyin")].join("");
  $("#overview-platforms").innerHTML = [platformCard("bilibili", true), platformCard("youtube", true), platformCard("douyin", true)].join("");
  bindPlatformActions();
}

function setFieldValue(key, value) {
  const element = document.querySelector(`[data-config="${key}"]`);
  if (!element) return;
  if (element.dataset.kind === "switch") element.checked = Boolean(value);
  else element.value = Array.isArray(value) ? value.join(", ") : value ?? "";
}

function applyConfig(config) { Object.entries(config || {}).forEach(([key, value]) => setFieldValue(key, value)); }

function collectSettings() {
  const settings = {};
  $$('[data-config]').forEach((element) => {
    const key = element.dataset.config;
    if (element.dataset.kind === "switch") {
      settings[key] = element.checked;
    } else if (element.dataset.kind === "number") {
      settings[key] = element.value === "" ? "" : Number(element.value);
    } else if (element.dataset.list === "true") {
      const raw = element.value.replace(/，/g, ",").replace(/；/g, ",").replace(/;/g, ",");
      settings[key] = raw.split(",").map((s) => s.trim()).filter(Boolean);
    } else {
      settings[key] = element.value;
    }
  });
  return settings;
}

function updateBackground() {
  const bg = $("#page-background");
  const preview = $(".appearance-preview");
  const info = $("#background-info");
  const active = state.data?.background?.active;
  if (!active) {
    bg.classList.remove("active");
    bg.style.backgroundImage = "";
    preview.style.backgroundImage = "";
    $("#preview-label").textContent = "默认背景";
    info.textContent = "尚未设置自定义背景";
    return;
  }
  const pluginName = encodeURIComponent(state.context?.pluginName || "astrbot_plugin_bilivideo_custom");
  const stamp = state.data.background.updated_at || Date.now();
  const url = `${location.origin}/api/v1/plugins/extensions/${pluginName}/ui/background?ts=${stamp}`;
  bg.style.backgroundImage = `url("${url}")`;
  bg.classList.add("active");
  preview.style.backgroundImage = `linear-gradient(180deg, rgba(12,27,52,.12), rgba(12,27,52,.68)), url("${url}")`;
  $("#preview-label").textContent = "自定义背景";
  info.textContent = `${state.data.background.filename || "背景图"} · ${state.data.background.content_type || "image"}`;
}

function updateStatus() {
  const s = state.data?.status || {};
  const running = s.scheduler_running;
  $("#runtime-badge").className = `status-pill ${running ? "success" : "warning"}`;
  $("#runtime-badge").textContent = running ? "运行中" : "待机";
  $("#platform-summary").textContent = s.multi_platform ? "多平台已启用" : "多平台未启用";
  $("#platform-summary").className = `soft-badge ${s.multi_platform ? "success" : ""}`;
  $("#bili-status") && ($("#bili-status").innerHTML = statusPill(s.bilibili_logged_in ? "已登录" : "未登录", s.bilibili_logged_in ? "success" : "warning"));
  $("#youtube-status") && ($("#youtube-status").innerHTML = statusPill(s.youtube_cookies ? "Cookies 已保存" : "未配置", s.youtube_cookies ? "success" : "warning"));
  $("#douyin-status") && ($("#douyin-status").innerHTML = statusPill(s.multi_platform ? "已启用" : "未启用", s.multi_platform ? "success" : "warning"));
  $("#bili-cookie-keys") && ($("#bili-cookie-keys").textContent = s.bilibili_cookie_keys?.length ? s.bilibili_cookie_keys.join("、") : "暂无");
  if ($("#overview-platforms")) $("#overview-platforms").innerHTML = [platformCard("bilibili", true), platformCard("youtube", true), platformCard("douyin", true)].join("");
  const enabledCount = [true, s.youtube_cookies, s.multi_platform].filter(Boolean).length;
  $("#overview-metrics").innerHTML = [`<div class="metric-card"><div class="metric-label">已接入平台</div><div class="metric-value">${enabledCount}<small>/ 3</small></div><div class="metric-meta">Bilibili 默认可用</div></div>`, `<div class="metric-card"><div class="metric-label">当前订阅</div><div class="metric-value">${s.subscription_count || 0}</div><div class="metric-meta">动态监测目标</div></div>`, `<div class="metric-card"><div class="metric-label">调度状态</div><div class="metric-value">${running ? "ON" : "OFF"}</div><div class="metric-meta">自动推送轮询</div></div>`, `<div class="metric-card"><div class="metric-label">输出主题</div><div class="metric-value">${state.data?.config?.theme === "dark" ? "暗" : "亮"}</div><div class="metric-meta">当前卡片渲染主题</div></div>`].join("");
}

function showToast(message, error = false) {
  const toast = $("#toast"); toast.textContent = message; toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => { toast.className = "toast"; }, 3400);
}

async function loadState() {
  try {
    state.data = await bridge.apiGet("ui/state");
    applyConfig(state.data.config);
    updateStatus();
    updateBackground();
  } catch (error) { showToast(error.message || "无法读取插件状态", true); $("#runtime-badge").textContent = "连接失败"; }
}

async function saveAll() {
  const button = $("#save-button"); button.disabled = true;
  try {
    const result = await bridge.apiPost("settings/save", { settings: collectSettings() });
    await loadState();
    showToast(result.restart_required ? "设置已保存；模型类设置建议重载插件" : "全部设置已保存并应用");
  } catch (error) { showToast(error.message || "保存失败", true); }
  finally { button.disabled = false; }
}

async function saveBilibili() {
  const cookies = { SESSDATA: $("#bili-sessdata").value.trim(), bili_jct: $("#bili-jct").value.trim(), DedeUserID: $("#bili-uid").value.trim(), DedeUserID__ckMd5: $("#bili-uid-md5").value.trim() };
  if (!cookies.SESSDATA) return showToast("请填写 SESSDATA", true);
  try { await bridge.apiPost("platform/bilibili/cookies", { cookies }); await loadState(); showToast("B站凭据已保存"); }
  catch (error) { showToast(error.message || "B站凭据保存失败", true); }
}

async function logoutBilibili() { try { await bridge.apiPost("platform/bilibili/logout", {}); await loadState(); showToast("B站凭据已清除"); } catch (error) { showToast(error.message || "退出失败", true); } }
async function saveYouTube() { const content = $("#youtube-cookies").value.trim(); if (!content) return showToast("请粘贴 YouTube cookies", true); try { const result = await bridge.apiPost("platform/youtube/cookies", { content }); await loadState(); showToast(`YouTube cookies 已保存（${result.cookie_count || 0} 条）`); } catch (error) { showToast(error.message || "YouTube cookies 保存失败", true); } }
async function logoutYouTube() { try { await bridge.apiPost("platform/youtube/logout", {}); await loadState(); showToast("YouTube cookies 已清除"); } catch (error) { showToast(error.message || "清除失败", true); } }

async function uploadBackground(file) {
  if (!file) return;
  try { await bridge.upload("ui/background/upload", file); await loadState(); showToast("背景图已更新"); }
  catch (error) { showToast(error.message || "背景图上传失败", true); }
}
async function resetBackground() { try { await bridge.apiPost("ui/background/reset", {}); await loadState(); showToast("已恢复默认背景"); } catch (error) { showToast(error.message || "恢复失败", true); } }

function setView(target) {
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.target === target));
  $$(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === target));
  if (state.context?.pageName) history.replaceState(null, "", `#${target}`);
}

function bindPlatformActions() {
  $("#save-bili")?.addEventListener("click", saveBilibili); $("#logout-bili")?.addEventListener("click", logoutBilibili);
  $("#save-youtube")?.addEventListener("click", saveYouTube); $("#logout-youtube")?.addEventListener("click", logoutYouTube);
}

function bindEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.target)));
  $$('[data-jump]').forEach((item) => item.addEventListener("click", () => setView(item.dataset.jump)));
  $("#refresh-button").addEventListener("click", loadState); $("#save-button").addEventListener("click", saveAll);
  $("#theme-toggle").addEventListener("click", () => { state.localTheme = state.localTheme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = state.localTheme; $("#theme-toggle").textContent = state.localTheme === "dark" ? "☀" : "☾"; });
  $("#background-file").addEventListener("change", (event) => uploadBackground(event.target.files?.[0])); $("#reset-background").addEventListener("click", resetBackground);
  $("#compact-mode").addEventListener("change", (event) => document.body.classList.toggle("compact-mode", event.target.checked));
  const uploadBox = $(".upload-box"); uploadBox.addEventListener("dragover", (event) => { event.preventDefault(); uploadBox.classList.add("dragging"); }); uploadBox.addEventListener("dragleave", () => uploadBox.classList.remove("dragging")); uploadBox.addEventListener("drop", (event) => { event.preventDefault(); uploadBox.classList.remove("dragging"); uploadBackground(event.dataTransfer.files?.[0]); });
}

try {
  state.context = await bridge.ready();
  renderAllFields(); renderPlatforms(); bindEvents();
  state.localTheme = state.context.isDark ? "dark" : "light"; document.documentElement.dataset.theme = state.localTheme;
  $("#theme-toggle").textContent = state.localTheme === "dark" ? "☀" : "☾";
  await loadState();
  bridge.onContext?.((context) => { state.context = context; if (state.localTheme === null) { state.localTheme = context.isDark ? "dark" : "light"; document.documentElement.dataset.theme = state.localTheme; } });
} catch (error) { showToast(error.message || "页面初始化失败", true); }

