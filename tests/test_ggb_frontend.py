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
      for (const key of ['mount', 'unmount', 'getBase64', 'isReady', 'loadEngine']) {
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


def test_ggb_module_has_no_bare_console_log():
    for lineno, line in enumerate(GGB_SOURCE.splitlines(), start=1):
        assert "console.log(" not in line, f"ggb.js:{lineno} uses a bare console.log"
