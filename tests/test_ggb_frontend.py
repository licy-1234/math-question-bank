"""Run the shipped GeoGebra demonstration module against a minimal Node DOM.

The frontend is plain JS with no build step, so regressions are guarded the same
way the other frontend suites do it: drive the *shipped* source through a tiny
stdin harness (Windows command lines cap out around 32K, so the script never
goes into ``-e``).
"""

from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
STATIC_JS = ROOT / "static" / "js"


def read(name: str) -> str:
    return (STATIC_JS / name).read_text(encoding="utf-8")


def section(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


GGB_SOURCE = read("ggb.js")

# Minimal DOM/window stub the shipped module closes over. It is deliberately
# small: ggb.js only touches createElement / getElementById / head / body and a
# handful of globals at load time.
DOM_STUB = r"""
const assert = require('node:assert/strict');

function makeEl(tag) {
  const el = {
    tagName: tag || 'div', parentNode: null, children: [], dataset: {},
    style: {}, value: '', id: '', className: '', innerHTML: '', textContent: '',
    _attrs: {},
    classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      toggle(x, force) {
        const on = force === undefined ? !this._s.has(x) : !!force;
        if (on) this._s.add(x); else this._s.delete(x);
        return on;
      },
      contains(x) { return this._s.has(x); }
    },
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null; },
    removeAttribute(k) { delete this._attrs[k]; },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; return c; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); if (this.id) elements.delete(this.id); },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    focus() {}, click() {}, closest() { return null; },
    get firstChild() { return this.children[0] || null; }
  };
  return el;
}

const elements = new Map();
const bodyEl = makeEl('body');
const headEl = makeEl('head');
const document = {
  body: bodyEl, head: headEl,
  getElementById(id) { return elements.get(id) || null; },
  createElement(tag) { return makeEl(tag); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {}
};

const window = {
  location: { href: 'http://localhost:8000/', origin: 'http://localhost:8000' },
  document,
  MathBankDebug: { debug() {}, info() {}, isEnabled() { return false; } },
  MathBankSafe: {
    escapeText: v => String(v == null ? '' : v),
    escapeAttribute: v => String(v == null ? '' : v),
    safeImageUrl: v => (v ? String(v) : '')
  },
  showToast() {}, renderMathInElement() {},
  btoa: globalThis.btoa, atob: globalThis.atob,
  confirm: () => true, prompt: () => null,
  requestAnimationFrame: fn => { if (typeof fn === 'function') fn(); }
};
window.window = window;
// 默认 fetch 桩：单元测试不得访问外网。prewarm 现在会尝试预热 8 MB 引擎主体，
// 没有桩就会真的打 geogebra.org。需要真实响应的测试自行覆盖 window.fetch。
globalThis.fetch = async () => ({ ok: false, status: 0, headers: { get: () => null } });
"""


def run_js(checks: str, sources: str = GGB_SOURCE) -> None:
    node = shutil.which("node")
    assert node, "Node is required for the isolated frontend regression checks"
    script = DOM_STUB + "\n" + sources + "\n(async () => {\n" + checks + \
        "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
    # The whole harness must travel over stdin on Windows, never as -e.
    result = subprocess.run([node, "-"], input=script, capture_output=True,
                            text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr + result.stdout


# --------------------------------------------------------------------------
# Frozen namespaces
# --------------------------------------------------------------------------

def test_frozen_namespaces_and_entry_points_are_exposed():
    run_js(r"""
      assert.equal(typeof window.renderGgbWorkspace, 'function');
      assert.equal(typeof window.GgbPanel, 'object');
      for (const key of ['render', 'renderAssetCard', 'mountQuestionPanel', 'unmount',
                         'openUploadModal', 'openLinkEditor', 'downloadAsset']) {
        assert.equal(typeof window.GgbPanel[key], 'function', 'GgbPanel.' + key);
      }
      assert.equal(window.GgbPanel.render, window.renderGgbWorkspace);
      assert.equal(typeof window.GgbEmbed, 'object');
      for (const key of ['mount', 'unmount', 'getBase64', 'isReady', 'loadEngine',
                         'prewarm', 'prewarmEngineBody', 'isEngineReady', 'isEngineBodyReady',
                         'resize']) {
        assert.equal(typeof window.GgbEmbed[key], 'function', 'GgbEmbed.' + key);
      }
      assert.equal(typeof window.GgbEmbed.GGB_CODEBASE, 'string');
      // 必须钉在 Classic 6 现代版对应的具体构建上；否则 deployggb.js 会用它自己的
      // 默认分支，可能与教师桌面版不一致。整版本号锁死，升级须同步改这里与 main.py。
      assert.equal(window.GgbEmbed.GGB_CODEBASE,
        'https://www.geogebra.org/apps/5.4.932.1/web3d',
        window.GgbEmbed.GGB_CODEBASE);
    """)


def test_deployggb_is_injected_lazily_not_statically():
    run_js(r"""
      // 加载后引擎脚本不得立即注入（懒注入），直到首次 mount/loadEngine。
      assert.equal(document.head.children.length, 0);
      assert.equal(window.GgbEmbed.isReady(), false);
      assert.equal(typeof window.GgbEmbed.loadEngine, 'function');
    """)


def _backend_codebase() -> str:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^_GGB_CODEBASE\s*=\s*"([^"]+)"', source, re.MULTILINE)
    assert match, "main.py 里找不到 _GGB_CODEBASE 赋值"
    return match.group(1)


def test_backend_and_frontend_codebase_stay_in_sync():
    """导出模板（后端）与嵌入引擎（前端）必须钉同一个构建，否则两边会各自漂移。"""

    backend = _backend_codebase()
    frontend = re.search(r"GGB_CODEBASE\s*=\s*'([^']+)'", GGB_SOURCE)
    assert frontend, "ggb.js 里找不到 GGB_CODEBASE 赋值"
    assert backend == frontend.group(1), (
        f"后端 _GGB_CODEBASE={backend!r} 与前端 GGB_CODEBASE={frontend.group(1)!r} 不一致"
    )
    assert backend == "https://www.geogebra.org/apps/5.4.932.1/web3d", (
        "引擎未钉在 Classic 6 现代版对应构建 5.4.932.1 上"
    )


# --------------------------------------------------------------------------
# Mount / unmount lifecycle (anti-leak)
# --------------------------------------------------------------------------

MOUNT_HARNESS = r"""
const engineState = { codebase: null, injectedId: null, removed: 0, lastParams: null };
window.GGBApplet = function (parameters, useHTML5) {
  this.parameters = parameters;
  this.useHTML5 = useHTML5;
  engineState.lastParams = parameters;
  this.inject = id => { engineState.injectedId = id; };
  this.remove = function () { engineState.removed += 1; };
  this.setHTML5Codebase = function (url) { engineState.codebase = url; };
};
window.fetch = async () => ({ ok: true, arrayBuffer: async () => new Uint8Array([1, 2, 3, 4]).buffer });
globalThis.fetch = window.fetch;
function freshHost() { const host = document.createElement('div'); return host; }
"""


def test_mount_injects_pinned_classic6_readonly_applet_from_base64():
    run_js(MOUNT_HARNESS + r"""
      const host = freshHost();
      const applet = await window.GgbEmbed.mount({ host, assetId: 7 });
      assert.ok(applet, 'mount returned an applet');
      const p = engineState.lastParams;
      assert.equal(p.appName, 'classic');
      assert.equal(p.language, 'zh_CN');
      assert.equal(p.useBrowserForJS, true);
      assert.equal(p.enableShiftDragZoom, true);
      // 只读预览：工具条 / 代数输入 / 菜单 / 重置 / 拖拽标签全关。
      assert.equal(p.showToolBar, false);
      assert.equal(p.showAlgebraInput, false);
      assert.equal(p.showMenuBar, false);
      assert.equal(p.showResetIcon, false);
      assert.equal(p.enableLabelDrags, false);
      // 主路径是 ggbBase64，不是 filename。
      assert.equal('filename' in p, false);
      assert.equal(typeof p.ggbBase64, 'string');
      assert.ok(p.ggbBase64.length > 0);
      // 版本号钉在 Classic 6 现代版对应的具体构建上。
      assert.equal(engineState.codebase, window.GgbEmbed.GGB_CODEBASE);
      assert.ok(engineState.injectedId, 'applet.inject received a container id');
      assert.equal(window.GgbEmbed.isReady(), true);
    """)


def test_editable_mount_turns_on_all_editing_chrome():
    run_js(MOUNT_HARNESS + r"""
      const host = freshHost();
      await window.GgbEmbed.mount({ host, assetId: 3, editable: true });
      const p = engineState.lastParams;
      assert.equal(p.showToolBar, true);
      assert.equal(p.showAlgebraInput, true);
      assert.equal(p.showMenuBar, true);
      assert.equal(p.showResetIcon, true);
      assert.equal(p.enableLabelDrags, true);
    """)


def test_unmount_calls_applet_remove_and_detaches_container():
    run_js(MOUNT_HARNESS + r"""
      const host = freshHost();
      await window.GgbEmbed.mount({ host, assetId: 11 });
      assert.equal(host.children.length, 1);
      window.GgbEmbed.unmount();
      assert.equal(engineState.removed, 1, 'applet.remove() called exactly once');
      assert.equal(host.children.length, 0, 'mount container detached');
      assert.equal(window.GgbEmbed.isReady(), false);
    """)


def test_remount_destroys_the_previous_applet():
    run_js(MOUNT_HARNESS + r"""
      const host = freshHost();
      await window.GgbEmbed.mount({ host, assetId: 1 });
      await window.GgbEmbed.mount({ host, assetId: 2 });
      // 第二次 mount 必须先销毁第一个，把实例数压在 0–1。
      assert.equal(engineState.removed, 1);
      assert.equal(host.children.length, 1);
    """)


def test_panel_unmount_delegates_to_embed_unmount():
    run_js(r"""
      // 切换到别的题目时把 GgbPanel.unmount 当统一退出入口，它必须走 window.GgbEmbed。
      let calls = 0;
      const original = window.GgbEmbed.unmount;
      window.GgbEmbed.unmount = function () { calls += 1; return original.apply(window.GgbEmbed, arguments); };
      window.GgbPanel.unmount();
      assert.equal(calls, 1);
    """)


def test_network_failure_falls_back_with_download_link():
    run_js(MOUNT_HARNESS + r"""
      // 模拟下载 .ggb 失败，走 mount 的 catch 兜底分支。
      window.fetch = async () => ({ ok: false, status: 502 });
      globalThis.fetch = window.fetch;
      const host = freshHost();
      const applet = await window.GgbEmbed.mount({ host, assetId: 42 });
      assert.equal(applet, null);
      const html = host.children[0].innerHTML;
      assert.ok(html.includes('无法加载 GeoGebra 组件'), html);
      assert.ok(html.includes('/api/ggb-assets/42/file'), html);
    """)


# --------------------------------------------------------------------------
# Loading performance: parallel file+engine, bounded cache, prewarming
# --------------------------------------------------------------------------

PARALLEL_HARNESS = r"""
const engineState = { injectedId: null, removed: 0, lastParams: null };
let fetchStarted = false;
let resolveFetch;
window.fetch = () => {
  fetchStarted = true;
  return new Promise(resolve => {
    resolveFetch = () => resolve({
      ok: true, arrayBuffer: async () => new Uint8Array([1, 2, 3, 4]).buffer
    });
  });
};
globalThis.fetch = window.fetch;
function freshHost() { return document.createElement('div'); }
"""


def test_mount_downloads_file_and_engine_in_parallel():
    """下载 .ggb 与注入引擎必须同时发起：串行会把两个下载周期叠加。"""

    run_js(PARALLEL_HARNESS + r"""
      const host = freshHost();
      const mountPromise = window.GgbEmbed.mount({ host, assetId: 9 });
      // 此刻两者都已发起、都未完成：脚本在 head 里，fetch 已调用，引擎未就绪。
      assert.equal(document.head.children.length, 1, 'deployggb.js injected');
      assert.ok(fetchStarted, '.ggb download started');
      assert.equal(typeof window.GGBApplet, 'undefined');
      // 先放行引擎：applet 仍不得注入（还在等文件）。
      window.GGBApplet = function (parameters) {
        engineState.lastParams = parameters;
        this.parameters = parameters;
        this.inject = id => { engineState.injectedId = id; };
        this.remove = function () { engineState.removed += 1; };
        this.setHTML5Codebase = function () {};
      };
      document.head.children[0].onload();
      await Promise.resolve();
      assert.ok(!engineState.injectedId, 'applet must wait for the file too');
      // 再放行文件下载：两者齐备才注入。
      resolveFetch();
      const applet = await mountPromise;
      assert.ok(applet, 'mount resolved once both tasks finished');
      assert.ok(engineState.injectedId, 'applet injected');
      assert.equal(typeof engineState.lastParams.ggbBase64, 'string');
    """)


def test_repeated_mount_reuses_cached_base64():
    """同一资源重复挂载不得重复下载 + 重复编码（有界缓存）。"""

    run_js(MOUNT_HARNESS + r"""
      let fetchCalls = 0;
      window.fetch = async () => {
        fetchCalls += 1;
        return { ok: true, arrayBuffer: async () => new Uint8Array([1, 2, 3, 4]).buffer };
      };
      globalThis.fetch = window.fetch;
      const host = freshHost();
      await window.GgbEmbed.mount({ host, assetId: 5 });
      await window.GgbEmbed.mount({ host, assetId: 5 });
      assert.equal(fetchCalls, 1, 'second mount of the same asset must not re-download');
    """)


def test_prewarm_is_idempotent_and_reports_engine_readiness():
    run_js(r"""
      assert.equal(window.GgbEmbed.isEngineReady(), false);
      window.GgbEmbed.prewarm();
      assert.equal(document.head.children.length, 1, 'prewarm injects deployggb.js');
      // 第二次预热不得重复注入脚本（幂等由「在途 Promise 复用」保证）。
      window.GgbEmbed.prewarm();
      assert.equal(document.head.children.length, 1, 'no duplicate injection');
      // 放行脚本加载（GGBApplet 仍缺失 → 走「未就绪」拒绝），清掉超时定时器，
      // 避免 node 进程被未决的 15 秒定时器拖住；prewarm 的 catch 会吞掉它。
      document.head.children[0].onload();
      await Promise.resolve();
      window.GGBApplet = function () {};
      assert.equal(window.GgbEmbed.isEngineReady(), true);
      const third = window.GgbEmbed.prewarm();
      assert.ok(third instanceof Promise, 'prewarm stays a promise when ready');
      assert.equal(document.head.children.length, 1, 'ready engine is not re-injected');
    """)


def test_workspace_open_and_card_intent_prewarm_the_engine():
    source = section(GGB_SOURCE, "async function renderGgbWorkspace",
                     "// ---------------- 命名空间导出")
    assert "prewarm()" in source, "打开工作区时应并行预热引擎"
    assert "pointerenter" in GGB_SOURCE, "卡片悬停应预热引擎"
    assert "'focus'" in GGB_SOURCE or '"focus"' in GGB_SOURCE, "卡片聚焦应预热引擎"


def test_embed_placeholder_replaces_the_bare_spinner():
    # 三处宿主（详情 / 题目侧 / 在线编辑）都必须走带预览图占位的 embedPlaceholderHtml。
    assert "function embedPlaceholderHtml" in GGB_SOURCE
    assert GGB_SOURCE.count("embedPlaceholderHtml") >= 5
    assert "ggb-embed-placeholder" in GGB_SOURCE
    assert "stateHtml('loading', '正在加载 GeoGebra 组件')" not in GGB_SOURCE
    # 占位文案必须对应真实阶段（下载引擎 / 初始化画布），不再谎报「正在读取 .ggb」。
    assert "正在下载 GeoGebra 引擎" in GGB_SOURCE
    assert "正在初始化画布" in GGB_SOURCE
    assert "正在读取 .ggb 并初始化画布" not in GGB_SOURCE
    assert "setPlaceholderStage" in GGB_SOURCE
    assert "appletOnLoad" in GGB_SOURCE
    # 详情区结构：GGB 占满主体（stage），预览图降级为下方参考条。
    assert "ggb-detail-stage" in GGB_SOURCE
    assert "ggb-preview-strip" in GGB_SOURCE
    assert "ggb-preview-figure" not in GGB_SOURCE


# 占位行为回归：截图里 applet 已渲染出工具栏/画布，占位层却仍盖在上面。
PLACEHOLDER_HARNESS = r"""
const engineState = { injectedId: null, lastParams: null };
window.GGBApplet = function (parameters) {
  engineState.lastParams = parameters;
  this.inject = id => { engineState.injectedId = id; };
  this.remove = function () {};
  this.setHTML5Codebase = function () {};
};
window.fetch = async () => ({ ok: true, arrayBuffer: async () => new Uint8Array([1]).buffer });
globalThis.fetch = window.fetch;
function placeholderEl() {
  const ph = document.createElement('div');
  ph.className = 'ggb-embed-placeholder';
  const title = document.createElement('strong');
  const desc = document.createElement('span');
  ph.querySelector = sel => sel === '.ui-state-title' ? title
    : sel === '.ui-state-description' ? desc : null;
  ph.titleEl = title;
  ph.descEl = desc;
  return ph;
}
function hostWithPlaceholder() {
  const host = document.createElement('div');
  host.querySelector = sel => sel === '.ggb-embed-placeholder'
    ? (host.children.find(c => String(c.className).indexOf('ggb-embed-placeholder') >= 0) || null)
    : null;
  host.appendChild(placeholderEl());
  return host;
}
"""


def test_placeholder_reports_the_real_stage_then_is_removed_when_applet_ready():
    """占位层必须在画布可交互后撤掉，且文案要对应真实阶段（截图缺陷回归）。"""

    run_js(PLACEHOLDER_HARNESS + r"""
      const host = hostWithPlaceholder();
      const ph = host.children[0];
      await window.GgbEmbed.mount({ host, assetId: 8 });
      // 引擎与文件齐备后进入「初始化画布」，而不是「正在读取 .ggb」。
      assert.equal(ph.titleEl.textContent, '正在初始化画布');
      assert.ok(ph.descEl.textContent.indexOf('载入演示文件') >= 0, ph.descEl.textContent);
      // appletOnLoad 必须把占位从 DOM 移除，否则它会一直盖在画布上。
      assert.equal(typeof engineState.lastParams.appletOnLoad, 'function');
      engineState.lastParams.appletOnLoad();
      const left = host.children.filter(c => String(c.className).indexOf('ggb-embed-placeholder') >= 0);
      assert.equal(left.length, 0, 'placeholder must be removed once the applet is ready');
      assert.equal(window.GgbEmbed.isReady(), true);
    """)


def test_prewarm_fetches_the_engine_body_into_the_http_cache():
    """8 MB 主体必须被真正抓取：deployggb.js 只是加载器，主包 inject 时才下。"""

    run_js(r"""
      const urls = [];
      window.fetch = async url => {
        urls.push(String(url));
        if (String(url).indexOf('web3d.nocache.js') >= 0) {
          return { ok: true, text: async () => 'x="A235CC712983FA478D93F2A05A5A8D23";' };
        }
        return {
          ok: true,
          headers: { get: () => '100' },
          body: null,
          arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer
        };
      };
      globalThis.fetch = window.fetch;
      window.GGBApplet = function () {};   // 加载器已就绪，聚焦主体预热
      await window.GgbEmbed.prewarm();
      assert.ok(urls.some(u => u.indexOf('/web3d.nocache.js') >= 0), urls.join(','));
      // 强名必须从 nocache.js 运行时提取，而不是硬编码某个构建。
      assert.ok(urls.some(u => u.indexOf('/A235CC712983FA478D93F2A05A5A8D23.cache.js') >= 0),
                urls.join(','));
      assert.equal(window.GgbEmbed.isEngineBodyReady(), true);
    """)


def test_index_html_links_the_ggb_stylesheet():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '/static/css/ggb.css' in html
    # ggb.css 必须在 app.css 之后：同特异性冲突时项目样式胜出。
    assert html.index('/static/css/app.css') < html.index('/static/css/ggb.css')


def test_ggb_stylesheet_covers_the_workspace_skeleton():
    css = (ROOT / "static" / "css" / "ggb.css").read_text(encoding="utf-8")
    for selector in ('.ggb-workspace-body', '.ggb-list-panel', '.ggb-detail-panel',
                     '.ggb-inspector-panel', '.ggb-embed-host', '.ggb-mobile-switch',
                     '.ggb-preview-strip', '.ggb-form-grid'):
        assert selector in css, f"ggb.css 缺少 {selector}"
    # 移动端必须走 data-mobile-pane 单栏切换（与 topic 同一断点）。
    assert 'data-mobile-pane' in css
    assert 'max-width: 960px' in css
    # GGB 宿主必须是定位上下文，applet 注入才能拿到确定像素尺寸。
    assert '.ggb-embed-host' in css and 'position: relative' in css


# --------------------------------------------------------------------------
# Question editor panel is lazy and question-switch destroys the applet
# --------------------------------------------------------------------------

def test_question_panel_mounts_only_when_host_exists_and_is_lazy():
    run_js(r"""
      // 没有挂载宿主时 mountQuestionPanel 不得抛错（页签未插入 DOM）。
      await window.GgbPanel.mountQuestionPanel(5);
      assert.equal(window.GgbEmbed.isReady(), false);
    """)


# --------------------------------------------------------------------------
# Rich-html pipeline untouched: iframe can never enter the question stem sink
# --------------------------------------------------------------------------

API_SOURCE = read("api.js")
SAFE_HELPER = section(API_SOURCE, "const MathBankSafe = (() =>", "window.MathBankSafe = MathBankSafe;")

# Faithful-enough DOMPurify stub: drop any tag outside RICH_HTML_CONFIG.ALLOWED_TAGS.
PURIFY_STUB = (
    "const window = { DOMPurify: { sanitize(html, config) {\n"
    "  const allowed = new Set(config.ALLOWED_TAGS);\n"
    "  return String(html).replace(/<\\/?([a-zA-Z][\\w-]*)\\b[^>]*>/g,\n"
    "    (m, tag) => allowed.has(tag.toLowerCase()) ? m : '');\n"
    "} } };\n"
)


def test_rich_html_pipeline_still_strips_iframe_and_script():
    script = PURIFY_STUB + SAFE_HELPER + r"""
const out = MathBankSafe.sanitizeRichHtml(
  '<div>ok</div><iframe src="https://evil.example"></iframe><script>alert(1)</script>');
if (out.includes('<iframe')) throw new Error('iframe leaked through the stem sink: ' + out);
if (out.includes('<script')) throw new Error('script leaked through the stem sink: ' + out);
if (!out.includes('<div>ok</div>')) throw new Error('allowlisted content was dropped: ' + out);
if (typeof window.MathBankSafe === 'object') { /* helper assigns window.MathBankSafe inside api.js */ }
"""
    node = shutil.which("node")
    assert node, "Node is required for the isolated frontend regression checks"
    result = subprocess.run([node, "-"], input=script, capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr + result.stdout


def test_rich_html_allowlist_excludes_embedding_tags():
    config = section(API_SOURCE, "const RICH_HTML_CONFIG = Object.freeze({", "function asString(value)")
    for tag in ("'iframe'", "'object'", "'embed'", "'script'"):
        assert tag not in config, f"{tag} must not be allowlisted in RICH_HTML_CONFIG"


def test_ggb_module_never_sanitizes_applet_markup():
    # GeoGebra markup must never travel through the stem sanitizer.
    assert "sanitizeRichHtml(" not in GGB_SOURCE
    assert "MathBankSafe.sanitizeRichHtml" not in GGB_SOURCE
    assert "DOMPurify.sanitize" not in GGB_SOURCE
    assert "<iframe" not in GGB_SOURCE
    # Destruction + attribution + pinned codebase are all in the shipped source.
    assert "applet.remove" in GGB_SOURCE
    assert "setHTML5Codebase(GGB_CODEBASE)" in GGB_SOURCE
    assert "Made with GeoGebra" in GGB_SOURCE
    assert "https://www.geogebra.org" in GGB_SOURCE


# --------------------------------------------------------------------------
# index.html wiring
# --------------------------------------------------------------------------

def test_index_html_declares_the_ggb_blocks():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "<!-- GGB_START" in html
    assert "<!-- GGB_END -->" in html
    assert html.count("<!-- GGB_START") == html.count("<!-- GGB_END -->")
    assert 'src="/static/js/ggb.js"' in html
    # ggb.js relies on paper.js having installed the wrapper layer first.
    assert html.index("/static/js/paper.js") < html.index("/static/js/ggb.js")
    # Workspace + editor panel + both tab affordances.
    assert 'id="ggbWorkspaceSection"' in html
    assert 'id="ggbListContainer"' in html
    assert 'id="ggbQuestionPanel"' in html
    assert 'data-editor-panel="ggb"' in html
    assert 'data-editor-panel-target="ggb"' in html
    # No static deployggb.js import: the engine is injected lazily by ggb.js.
    assert "deployggb.js" not in html


# --------------------------------------------------------------------------
# Cascade hook points are wired (source-level, cheap guards)
# --------------------------------------------------------------------------

def test_workspace_editor_and_import_hooks_are_wired():
    paper = read("paper.js")
    assert "ggbInnerSelectWorkspace" in paper
    assert "workspaceId === 'ggb'" in paper
    assert "window.renderGgbWorkspace()" in paper

    import_source = read("import.js")
    assert "window.GgbPanel.unmount()" in import_source
    assert "window.GgbPanel.mountQuestionPanel(fullItem.id)" in import_source

    editor = read("editor.js")
    assert "'review', 'ggb'" in editor
    assert "window.GgbPanel.mountQuestionPanel(EditorState.questionId)" in editor


def test_inner_select_workspace_reserves_a_ggb_branch():
    """内层 selectWorkspace 的 if 链必须给 ggb 显式占位。

    否则选「演示资源库」会落入 bank 兜底分支，两个工作区同时显示
    （这正是截图里「题库 + 演示资源库叠在一起」的根因）。
    """

    paper = read("paper.js")
    topic_at = paper.index("workspaceId === 'topic'")
    ggb_at = paper.index("workspaceId === 'ggb'", topic_at)
    bank_fallback_at = paper.index("closeBankDetail()", ggb_at)
    assert ggb_at < bank_fallback_at, "ggb 占位分支必须排在 bank 兜底 else 之前"


def test_ggb_module_has_no_bare_console_log():
    for lineno, line in enumerate(GGB_SOURCE.splitlines(), start=1):
        assert "console.log(" not in line, f"ggb.js:{lineno} uses a bare console.log"


def test_online_editor_modal_is_large_and_expandable():
    """在线编辑弹窗：大画布 + 最大化（偏好记忆）+ 三段式信息层级。"""

    # 弹窗壳：去掉固定 max-w-2xl，换成可最大化的 ggb-editor-modal。
    assert "ensureModalShell('ggbOnlineEditModal'" in GGB_SOURCE
    assert "shell.classList.remove('max-w-2xl'" in GGB_SOURCE
    assert "'ggb-editor-modal'" in GGB_SOURCE
    assert "is-expanded" in GGB_SOURCE
    assert "ggb-editor-expanded" in GGB_SOURCE, "最大化偏好须持久化"
    # 结构：元信息 chips → 画布主体 → 底部按钮。
    for marker in ('ggb-editor-meta', 'ggb-editor-chip', 'ggb-editor-panel',
                   'ggb-editor-footnote', 'ggb-editor-expand'):
        assert marker in GGB_SOURCE, marker
    # 容器尺寸变化后必须让 GGB 重新测量（否则最大化后画布不跟着变）。
    # 注意：尺寸接口在全局 ggbApplet 上，GGBApplet 构造函数上没有。
    assert "GgbEmbed.resize()" in GGB_SOURCE
    assert "window.ggbApplet" in GGB_SOURCE
    assert "applet_scaler" in GGB_SOURCE
    # 关闭编辑器后工作区预览要挂回来（mount 是单例，编辑器会销毁工作区 applet）。
    assert "restoreWorkspacePreview" in GGB_SOURCE

    css = (ROOT / "static" / "css" / "ggb.css").read_text(encoding="utf-8")
    assert ".ggb-editor-modal" in css
    assert "min(1520px, 96vw)" in css
    assert "is-expanded" in css and "96vw" in css
    assert ".ggb-editor-body" in css and "flex-direction: column" in css
    assert ".ggb-editor-panel" in css
