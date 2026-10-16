// ggb.js —— GeoGebra 动态演示资源库 IIFE 模块（照 topic.js / paper.js 先例）
// 必须在 paper.js 之后加载：selectWorkspace 的 ggb 分支在 paper.js 中。
// 调试纪律：所有 verbose 日志走 window.MathBankDebug（localStorage mathbank_debug_logs 门控），
// 只有真实错误才 console.error，绝不使用裸 console.log。
//
// 安全边界：题干 HTML 走 api.js 的 DOMPurify 白名单，白名单内没有 iframe/object/embed，
// 因此 GeoGebra 一律走本模块程序化创建的独立受控容器（GgbEmbed），
// 本文件绝不调用 sanitizeRichHtml 处理任何 applet 标记，题干 markdown 中也不出现 GGB 标记。
(function () {
    'use strict';
    const debug = window.MathBankDebug;
    const safe = window.MathBankSafe;

    // === GeoGebra 引擎版本常量（全模块唯一可配置处） ===
    // 2026-10-16 由 Classic 5 的 5.4.907.0 迁移到 Classic 6 现代版对应构建 5.4.932.1。
    // 版本线说明：GeoGebra 桌面 Calculator Suite / Classic 6 是 6.0.x，网页可嵌入引擎
    // （GWT/HTML5 线）是 5.4.x，两者同构建号同步发布——5.4.932.1（2026-10-09）即桌面
    // 6.0.932.1 的网页对应构建，与 /apps/latest/web3d/ 字节级一致（MD5 1f296cf6…），
    // 也就是 geogebra.org/classic 当今运行的引擎；网页侧不存在 6.x 引擎（已探测，全 403）。
    // 旧值 5.4.907.0 已被 CDN 下线（HTTP 403），这是本次必须迁移的直接原因。
    // 引擎升级时「只改这一处」即可整体切换（勿散落到各调用点）。
    const GGB_CODEBASE = 'https://www.geogebra.org/apps/5.4.932.1/web3d';
    // deployggb.js 走官方 CDN 动态注入（不在 index.html 里静态引入），带超时与网络失败兜底。
    // 它是版本无关的官方加载器：实际加载哪个模块由 codebase 路径末段决定（这里是 web3d）。
    const DEPLOYGGB_SRC = 'https://www.geogebra.org/apps/deployggb.js';
    const ENGINE_TIMEOUT_MS = 8000;

    // ---------------- 词表（本地兜底；后端 /api/config/ggb-link-types 就绪后以其为准） ----------------

    const LINK_LABELS = {
        demo: '直观演示', steps: '解题步骤', variant: '变式探究',
        diagnosis: '错题诊断', task: '课堂任务'
    };
    const LINK_CODES = ['demo', 'steps', 'variant', 'diagnosis', 'task'];
    const STATUS_LABELS = { active: '启用', archived: '已归档', draft: '草稿' };

    const GgbStore = {
        config: null,
        assets: [],
        currentAssetId: null,
        detail: null,
        questionLinks: [],
        currentQuestionId: null,
        keyword: '',
        dimFilter: '',
        codeFilter: '',
        page: 1,
        pageSize: 24,
        total: 0,
        loadingList: false,
        loadingDetail: false,
        searchTimer: null
    };

    // ---------------- API 薄封装（写操作的 X-Local-Token 由 api.js 的 fetch 封装自动注入） ----------------

    function requestError(payload, status) {
        let message = (payload && (payload.detail || payload.message)) || `请求失败 ${status}`;
        if (typeof message !== 'string') message = JSON.stringify(message);
        const error = new Error(message);
        error.status = status;
        return error;
    }

    async function api(path, options = {}) {
        const opts = Object.assign({}, options);
        const headers = Object.assign({}, opts.headers || {});
        if (opts.json !== undefined) {
            headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(opts.json);
            delete opts.json;
        }
        const response = await fetch(path, Object.assign({}, opts, { headers }));
        let payload = null;
        try { payload = await response.json(); } catch (e) { payload = null; }
        if (!response.ok) throw requestError(payload, response.status);
        return payload || {};
    }

    // multipart 提交：不设置 Content-Type（交给浏览器带 boundary）。
    async function apiForm(path, formData, method = 'POST') {
        const response = await fetch(path, { method, body: formData });
        let payload = null;
        try { payload = await response.json(); } catch (e) { payload = null; }
        if (!response.ok) throw requestError(payload, response.status);
        return payload || {};
    }

    // ---------------- 通用小工具 ----------------

    function toast(message, type) {
        if (window.showToast) window.showToast(message, type || 'info');
    }

    function escapeText(value) {
        return safe ? safe.escapeText(String(value == null ? '' : value)) : String(value == null ? '' : value);
    }

    function escapeAttribute(value) {
        return safe ? safe.escapeAttribute(String(value == null ? '' : value)) : String(value == null ? '' : value);
    }

    function imageUrl(value) {
        return safe && typeof safe.safeImageUrl === 'function' ? safe.safeImageUrl(value) : '';
    }

    function renderMath(element) {
        if (!element || typeof window.renderMathInElement !== 'function') return;
        try {
            window.renderMathInElement(element, {
                delimiters: [
                    { left: '$$', right: '$$', display: true },
                    { left: '$', right: '$', display: false },
                    { left: '\\(', right: '\\)', display: false },
                    { left: '\\[', right: '\\]', display: true }
                ],
                throwOnError: false
            });
        } catch (e) {
            console.error('[Ggb] KaTeX rendering error:', e);
        }
    }

    function formatBytes(size) {
        const value = Number(size);
        if (!Number.isFinite(value) || value <= 0) return '—';
        if (value < 1024) return `${value} B`;
        if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
        return `${(value / 1024 / 1024).toFixed(1)} MB`;
    }

    function formatTime(value) {
        if (!value) return '';
        return String(value).replace('T', ' ').slice(0, 16);
    }

    // 触发浏览器下载（临时 <a download>，不改动页面状态）。
    function triggerDownload(url, filename) {
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', filename || '');
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    function downloadAsset(assetOrId) {
        const asset = assetOrId && typeof assetOrId === 'object'
            ? assetOrId
            : { id: assetOrId, title: 'ggb' };
        if (!asset || !asset.id) return;
        const name = `${String(asset.title || '演示').replace(/[\\/:*?"<>|]/g, '_')}.ggb`;
        triggerDownload(`/api/ggb-assets/${asset.id}/file`, name);
    }

    // 法律要求：GeoGebra 署名在任何预览容器与导出入口都必须保留（不使用其 logo）。
    function attributionHtml() {
        return '<p class="ggb-attribution">Made with GeoGebra® · '
            + '<a href="https://www.geogebra.org" target="_blank" rel="noopener noreferrer">www.geogebra.org</a></p>';
    }

    // ---------------- 模态层（与 topic.js 同构） ----------------

    function openModal(id) {
        const modal = document.getElementById(id);
        if (!modal) return null;
        document.body.classList.add('modal-active');
        modal.classList.remove('hidden');
        requestAnimationFrame(() => modal.classList.remove('opacity-0'));
        modal.setAttribute('aria-hidden', 'false');
        return modal;
    }

    function closeModal(id) {
        const modal = document.getElementById(id);
        if (!modal) return;
        modal.classList.add('opacity-0');
        modal.setAttribute('aria-hidden', 'true');
        setTimeout(() => {
            modal.classList.add('hidden');
            if (!document.querySelector('.glass-modal-overlay:not(.hidden)')) {
                document.body.classList.remove('modal-active');
            }
        }, 300);
    }

    function ensureModalShell(id, title, bodyHtml, footerHtml) {
        let modal = document.getElementById(id);
        if (modal) modal.remove();
        modal = document.createElement('div');
        modal.id = id;
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-hidden', 'true');
        modal.className = 'fixed inset-0 glass-modal-overlay z-50 flex items-center justify-center hidden opacity-0 transition-opacity duration-300 p-4';
        modal.innerHTML = `
            <div class="glass-modal rounded-2xl shadow-xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden" role="document">
                <header class="flex items-center justify-between px-5 py-4 border-b border-slate-200/70">
                    <h3 class="font-bold text-sm text-slate-800">${escapeText(title)}</h3>
                    <button type="button" class="text-slate-400 hover:text-slate-600 p-1" aria-label="关闭" data-ggb-modal-close="${id}">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </header>
                <div class="flex-1 overflow-y-auto custom-scrollbar p-5" data-ggb-modal-body>${bodyHtml || ''}</div>
                ${footerHtml ? `<footer class="px-5 py-3 border-t border-slate-200/70 flex justify-end gap-2" data-ggb-modal-footer>${footerHtml}</footer>` : ''}
            </div>`;
        document.body.appendChild(modal);
        modal.addEventListener('click', (event) => {
            if (event.target === modal) closeModal(id);
            const closer = event.target.closest && event.target.closest(`[data-ggb-modal-close="${id}"]`);
            if (closer) closeModal(id);
        });
        return modal;
    }

    // ---------------- GeoGebra 嵌入式引擎（独立受控容器 + 懒挂载 + 严格销毁） ----------------

    const GgbEmbed = (() => {
        // 全局单例注入 Promise，防止重复注入 deployggb.js。
        let enginePromise = null;
        let activeApplet = null;
        let activeMountNode = null;
        let activeBase64 = null;
        let seq = 0;

        function uniqueHostId() {
            seq += 1;
            return `ggb-applet-${Date.now()}-${seq}`;
        }

        // 动态注入官方 CDN 的 deployggb.js，带 onerror 与 8 秒超时。
        function loadEngine() {
            if (typeof window.GGBApplet !== 'undefined' && window.GGBApplet) {
                return Promise.resolve(window.GGBApplet);
            }
            if (enginePromise) return enginePromise;
            enginePromise = new Promise((resolve, reject) => {
                let settled = false;
                const finish = (fn, arg) => {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timer);
                    fn(arg);
                };
                const timer = setTimeout(() => {
                    enginePromise = null;
                    finish(reject, new Error('GeoGebra 引擎加载超时'));
                }, ENGINE_TIMEOUT_MS);
                const script = document.createElement('script');
                script.src = DEPLOYGGB_SRC;
                script.async = true;
                script.onload = () => {
                    if (window.GGBApplet) finish(resolve, window.GGBApplet);
                    else { enginePromise = null; finish(reject, new Error('GeoGebra 引擎未就绪')); }
                };
                script.onerror = () => {
                    enginePromise = null;
                    finish(reject, new Error('GeoGebra 引擎下载失败'));
                };
                document.head.appendChild(script);
            });
            return enginePromise;
        }

        function arrayBufferToBase64(buffer) {
            const bytes = new Uint8Array(buffer);
            let binary = '';
            const chunk = 0x8000;
            for (let i = 0; i < bytes.length; i += chunk) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
            }
            return window.btoa(binary);
        }

        // 主路径：下载当前 .ggb → base64（不用 filename 作为主路径）。
        async function getBase64(assetId, versionNo) {
            const path = versionNo
                ? `/api/ggb-assets/${assetId}/versions/${versionNo}/file`
                : `/api/ggb-assets/${assetId}/file`;
            const response = await fetch(path);
            if (!response.ok) throw new Error(`下载 .ggb 失败 ${response.status}`);
            const buffer = await response.arrayBuffer();
            return arrayBufferToBase64(buffer);
        }

        function showLoadFailure(container, assetId) {
            if (!container) return;
            container.innerHTML = `
                <div class="ggb-embed-fallback" role="status">
                    <span class="ggb-embed-fallback-icon" aria-hidden="true"><i class="fa-solid fa-triangle-exclamation"></i></span>
                    <span class="ggb-embed-fallback-text">无法加载 GeoGebra 组件（当前网络不可用）。可点「下载 .ggb」用本地 GeoGebra Classic 6 打开。</span>
                    <a class="ggb-embed-fallback-link" href="/api/ggb-assets/${encodeURIComponent(String(assetId || ''))}/file" download><i class="fa-solid fa-download" aria-hidden="true"></i><span>下载 .ggb</span></a>
                </div>`;
        }

        // 严格销毁：优先 applet.remove()；不可用时退化为清空容器 DOM 并释放引用。
        function unmount() {
            const applet = activeApplet;
            const node = activeMountNode;
            activeApplet = null;
            activeMountNode = null;
            activeBase64 = null;
            if (applet && typeof applet.remove === 'function') {
                try {
                    applet.remove();
                } catch (error) {
                    debug.debug('GGB applet.remove() failed:', error && error.message);
                }
            }
            if (node) {
                try {
                    while (node.firstChild) node.removeChild(node.firstChild);
                } catch (e) { /* 容器已分离，忽略 */ }
                if (node.parentNode) node.parentNode.removeChild(node);
            }
        }

        function isReady() {
            return activeApplet != null;
        }

        async function mount(options = {}) {
            const host = options.host;
            const assetId = options.assetId;
            const editable = options.editable === true;
            // 每次 mount 先销毁旧实例，把 applet 实例数长期压在 0–1 个。
            unmount();
            if (!host || !assetId) return null;

            const mountNode = document.createElement('div');
            mountNode.className = 'ggb-applet-host';
            mountNode.id = uniqueHostId();
            host.appendChild(mountNode);
            activeMountNode = mountNode;

            try {
                const base64 = options.base64 || await getBase64(assetId, options.versionNo);
                if (activeMountNode !== mountNode) return null; // 已被后续 mount 取代
                activeBase64 = base64;
                const GGBApplet = await loadEngine();
                if (activeMountNode !== mountNode) return null;
                const parameters = {
                    appName: 'classic',
                    language: 'zh_CN',
                    useBrowserForJS: true,
                    enableShiftDragZoom: true,
                    // 只读预览全部关闭；可编辑（V2.0）全部打开。
                    showToolBar: editable,
                    showAlgebraInput: editable,
                    showMenuBar: editable,
                    showResetIcon: editable,
                    enableLabelDrags: editable,
                    ggbBase64: base64
                };
                const applet = new GGBApplet(parameters, true);
                if (applet && typeof applet.setHTML5Codebase === 'function') {
                    // 钉住具体构建（5.4.932.1 = Classic 6 桌面版的网页对应引擎），
                    // 避免默认分支漂移导致与教师桌面版不一致。
                    applet.setHTML5Codebase(GGB_CODEBASE);
                }
                applet.inject(mountNode.id);
                activeApplet = applet;
                return applet;
            } catch (error) {
                debug.debug('GGB mount failed:', error && error.message);
                showLoadFailure(mountNode, assetId);
                return null;
            }
        }

        return { loadEngine, mount, unmount, getBase64, isReady, GGB_CODEBASE };
    })();
    window.GgbEmbed = GgbEmbed;

    // ---------------- 配置与列表 ----------------

    async function loadConfig() {
        if (GgbStore.config) return GgbStore.config;
        try {
            const result = await api('/api/config/ggb-link-types');
            GgbStore.config = result.data || {};
        } catch (error) {
            debug.debug('GGB config unavailable, using local fallback:', error && error.message);
            GgbStore.config = {};
        }
        return GgbStore.config;
    }

    function linkLabel(code) {
        const labels = (GgbStore.config && GgbStore.config.link_labels) || LINK_LABELS;
        return labels[code] || LINK_LABELS[code] || code || '';
    }

    function linkCodes() {
        const codes = GgbStore.config && GgbStore.config.link_codes;
        return Array.isArray(codes) && codes.length ? codes : LINK_CODES;
    }

    // 归一化 {code,label} 与纯字符串两种词表形态。
    function normalizeVocabulary(values, fallback) {
        if (!Array.isArray(values) || !values.length) return fallback;
        return values.map((item) => {
            if (item && typeof item === 'object') {
                const code = item.code || item.value || item.id || '';
                return { code: String(code), label: String(item.label || item.name || code) };
            }
            return { code: String(item), label: String(item) };
        }).filter(entry => entry.code);
    }

    function dimOptions() {
        return normalizeVocabulary(GgbStore.config && GgbStore.config.tag_dims, []);
    }

    function dynamicTypeOptions() {
        return normalizeVocabulary(GgbStore.config && GgbStore.config.dynamic_types, []);
    }

    async function loadAssetList() {
        GgbStore.loadingList = true;
        renderListPanel();
        try {
            const params = new URLSearchParams();
            if (GgbStore.keyword) params.set('q', GgbStore.keyword);
            if (GgbStore.dimFilter) params.set('dim', GgbStore.dimFilter);
            if (GgbStore.codeFilter) params.set('code', GgbStore.codeFilter);
            params.set('page', String(GgbStore.page));
            params.set('page_size', String(GgbStore.pageSize));
            const result = await api(`/api/ggb-assets?${params.toString()}`);
            const data = result.data;
            if (Array.isArray(data)) {
                GgbStore.assets = data;
                GgbStore.total = data.length;
            } else {
                GgbStore.assets = (data && data.items) || [];
                GgbStore.total = (data && (data.total || data.count)) || GgbStore.assets.length;
            }
        } catch (error) {
            console.error('[Ggb] 加载演示资源列表失败:', error);
            toast(error.message || '加载演示资源列表失败', 'error');
            GgbStore.assets = [];
            GgbStore.total = 0;
        }
        GgbStore.loadingList = false;
        renderListPanel();
    }

    // 资源卡片：只用预览图，绝不预建 applet。
    function renderAssetCard(asset) {
        const active = asset.id === GgbStore.currentAssetId;
        const preview = imageUrl(asset.preview_image_path);
        const thumb = preview
            ? `<img class="ggb-card-thumb" src="${escapeAttribute(preview)}" alt="${escapeAttribute(asset.title || '演示预览')}" loading="lazy" decoding="async">`
            : '<span class="ggb-card-thumb ggb-card-thumb-placeholder" aria-hidden="true"><i class="fa-solid fa-chart-area"></i></span>';
        const badges = [
            asset.status && asset.status !== 'active'
                ? `<span class="ggb-card-badge">${escapeText(STATUS_LABELS[asset.status] || asset.status)}</span>`
                : '',
            asset.current_version_no ? `<span class="ggb-card-badge ggb-card-badge-muted">v${asset.current_version_no}</span>` : ''
        ].join('');
        return `
            <button type="button" class="ggb-card ${active ? 'is-active' : ''}" data-ggb-id="${asset.id}" aria-pressed="${active ? 'true' : 'false'}">
                ${thumb}
                <span class="ggb-card-main">
                    <span class="ggb-card-title">${escapeText(asset.title || '未命名演示')}</span>
                    <span class="ggb-card-meta">
                        <span>${formatBytes(asset.file_size)}</span>
                        <span>${escapeText(formatTime(asset.updated_at))}</span>
                    </span>
                </span>
                <span class="ggb-card-badges">${badges}</span>
            </button>`;
    }

    function stateHtml(kind, title, description) {
        const icon = kind === 'loading' ? 'fa-spinner fa-spin'
            : kind === 'error' ? 'fa-triangle-exclamation'
                : 'fa-chart-area';
        return `
            <div class="ui-state ui-state-${kind}" role="status">
                <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid ${icon}"></i></span>
                <strong class="ui-state-title">${escapeText(title)}</strong>
                <span class="ui-state-description">${escapeText(description || '')}</span>
            </div>`;
    }

    function renderListPanel() {
        const filters = document.getElementById('ggbListFilters');
        const container = document.getElementById('ggbListContainer');
        if (!container) return;

        if (filters && !filters.dataset.bound) {
            filters.dataset.bound = '1';
            filters.innerHTML = `
                <label for="ggbSearchInput" class="sr-only">搜索演示资源</label>
                <input id="ggbSearchInput" type="search" class="glass-input" placeholder="搜索演示标题或说明" value="${escapeAttribute(GgbStore.keyword)}">
                <div class="ggb-dim-chips" role="group" aria-label="按维度筛选" id="ggbDimChips"></div>`;
            filters.querySelector('#ggbSearchInput').addEventListener('input', (event) => {
                GgbStore.keyword = String(event.target.value || '').trim();
                clearTimeout(GgbStore.searchTimer);
                GgbStore.searchTimer = setTimeout(() => { GgbStore.page = 1; loadAssetList(); }, 250);
            });
            filters.querySelector('#ggbDimChips').addEventListener('click', (event) => {
                const chip = event.target.closest('.ggb-dim-chip');
                if (!chip) return;
                GgbStore.dimFilter = chip.dataset.ggbDim || '';
                filters.querySelectorAll('.ggb-dim-chip').forEach(c => c.classList.toggle('is-active', c === chip));
                GgbStore.page = 1;
                loadAssetList();
            });
        }

        const chipRow = filters && filters.querySelector('#ggbDimChips');
        if (chipRow && !chipRow.dataset.rendered) {
            chipRow.dataset.rendered = '1';
            const dims = dimOptions();
            chipRow.innerHTML = `<button type="button" class="ggb-dim-chip is-active" data-ggb-dim="">全部</button>`
                + dims.map(dim => `<button type="button" class="ggb-dim-chip" data-ggb-dim="${escapeAttribute(dim.code)}">${escapeText(dim.label)}</button>`).join('');
        }

        if (GgbStore.loadingList) {
            container.innerHTML = stateHtml('loading', '正在载入演示资源');
            return;
        }
        if (!GgbStore.assets.length) {
            const filtered = GgbStore.keyword || GgbStore.dimFilter;
            container.innerHTML = stateHtml('empty',
                filtered ? '没有匹配的演示资源' : '还没有演示资源',
                filtered ? '调整搜索词或维度筛选试试。' : '点击「上传 .ggb」添加第一个动态演示。');
            return;
        }
        container.innerHTML = `<div class="ggb-card-list" role="listbox" aria-label="演示资源列表">`
            + GgbStore.assets.map(renderAssetCard).join('')
            + '</div>';
        container.querySelectorAll('.ggb-card').forEach(button => {
            button.addEventListener('click', () => selectAsset(parseInt(button.dataset.ggbId, 10)));
        });
    }

    // ---------------- 资源详情 ----------------

    async function selectAsset(assetId) {
        GgbStore.currentAssetId = assetId;
        renderListPanel();
        await loadAssetDetail(assetId);
    }

    async function loadAssetDetail(assetId) {
        // 切换资源前先销毁上一个 applet，避免实例泄漏。
        GgbEmbed.unmount();
        GgbStore.loadingDetail = true;
        renderDetailPanel();
        try {
            const result = await api(`/api/ggb-assets/${assetId}`);
            GgbStore.detail = result.data;
        } catch (error) {
            console.error('[Ggb] 加载演示资源详情失败:', error);
            toast(error.message || '加载演示资源详情失败', 'error');
            GgbStore.detail = null;
        }
        GgbStore.loadingDetail = false;
        renderDetailPanel();
        renderInspector();
    }

    function renderDetailPanel() {
        const empty = document.getElementById('ggbDetailEmpty');
        const content = document.getElementById('ggbDetailContent');
        if (!empty || !content) return;
        if (!GgbStore.detail) {
            empty.classList.remove('hidden');
            content.classList.add('hidden');
            const title = empty.querySelector('.ui-state-title');
            const desc = empty.querySelector('.ui-state-description');
            if (GgbStore.loadingDetail) {
                if (title) title.textContent = '正在载入演示资源';
                if (desc) desc.textContent = '';
            } else if (GgbStore.currentAssetId) {
                if (title) title.textContent = '演示资源加载失败';
                if (desc) desc.textContent = '请稍后重试或重新选择。';
            } else {
                if (title) title.textContent = '还没有选择演示资源';
                if (desc) desc.textContent = '从左侧列表选择一个资源查看预览、标签与版本，或上传新的 .ggb。';
            }
            return;
        }
        empty.classList.add('hidden');
        content.classList.remove('hidden');

        const asset = GgbStore.detail.asset || {};
        const preview = imageUrl(asset.preview_image_path);
        const header = document.getElementById('ggbDetailHeader');
        header.innerHTML = `
            <div class="ggb-detail-title-row">
                <input id="ggbTitleInput" class="glass-input" value="${escapeAttribute(asset.title || '')}" aria-label="演示标题" maxlength="200">
                <select id="ggbStatusSelect" class="glass-select" aria-label="资源状态">
                    ${['active', 'archived', 'draft'].map(s => `<option value="${s}" ${s === asset.status ? 'selected' : ''}>${STATUS_LABELS[s] || s}</option>`).join('')}
                </select>
                <button type="button" class="glass-btn" id="ggbSaveMetaBtn"><i class="fa-solid fa-floppy-disk" aria-hidden="true"></i><span>保存信息</span></button>
            </div>
            <div class="ggb-detail-desc-row">
                <label for="ggbDescInput" class="sr-only">演示说明</label>
                <textarea id="ggbDescInput" class="glass-input" rows="2" placeholder="演示说明（可选）">${escapeText(asset.description || '')}</textarea>
            </div>
            <div class="ggb-detail-actions-row">
                <button type="button" class="glass-btn" id="ggbDownloadBtn"><i class="fa-solid fa-download" aria-hidden="true"></i><span>下载 .ggb</span></button>
                <button type="button" class="glass-btn" id="ggbEditOnlineBtn"><i class="fa-solid fa-pen-to-square" aria-hidden="true"></i><span>在线编辑</span></button>
                <button type="button" class="glass-btn" id="ggbSaveVersionBtn"><i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i><span>存新版本</span></button>
                <button type="button" class="glass-btn" id="ggbDuplicateBtn"><i class="fa-solid fa-clone" aria-hidden="true"></i><span>另存副本</span></button>
                <button type="button" class="glass-btn glass-btn-danger" id="ggbDeleteBtn"><i class="fa-solid fa-trash-can" aria-hidden="true"></i><span>删除</span></button>
            </div>`;
        bindDetailHeader(asset);

        const scroll = document.getElementById('ggbDetailScroll');
        scroll.innerHTML = `
            <div class="ggb-preview-figure">
                <div class="ggb-preview-thumb">
                    ${preview
                        ? `<img src="${escapeAttribute(preview)}" alt="${escapeAttribute(asset.title || '演示预览')}" loading="lazy" decoding="async">`
                        : '<span class="ggb-preview-thumb-placeholder" aria-hidden="true"><i class="fa-solid fa-image"></i><em>未上传预览图</em></span>'}
                </div>
                <div class="ggb-embed-panel">
                    <div class="ggb-embed-host" id="ggbAssetEmbedHost" role="region" aria-label="GeoGebra 动态演示预览"></div>
                    ${attributionHtml()}
                </div>
            </div>`;

        const host = document.getElementById('ggbAssetEmbedHost');
        if (host) {
            host.innerHTML = stateHtml('loading', '正在加载 GeoGebra 组件');
            GgbEmbed.mount({ host, assetId: asset.id });
        }
    }

    function bindDetailHeader(asset) {
        const saveBtn = document.getElementById('ggbSaveMetaBtn');
        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const title = document.getElementById('ggbTitleInput').value.trim();
                if (!title) { toast('标题不能为空', 'info'); return; }
                try {
                    await api(`/api/ggb-assets/${asset.id}`, {
                        method: 'PUT',
                        json: {
                            title,
                            description: document.getElementById('ggbDescInput').value,
                            status: document.getElementById('ggbStatusSelect').value
                        }
                    });
                    toast('演示信息已保存', 'success');
                    await loadAssetList();
                    await loadAssetDetail(asset.id);
                } catch (error) {
                    toast(error.message || '保存失败', 'error');
                }
            });
        }
        const downloadBtn = document.getElementById('ggbDownloadBtn');
        if (downloadBtn) downloadBtn.addEventListener('click', () => downloadAsset(asset));

        const editBtn = document.getElementById('ggbEditOnlineBtn');
        if (editBtn) editBtn.addEventListener('click', () => openOnlineEditor(asset));

        const versionBtn = document.getElementById('ggbSaveVersionBtn');
        if (versionBtn) versionBtn.addEventListener('click', () => saveNewVersion(asset));

        const duplicateBtn = document.getElementById('ggbDuplicateBtn');
        if (duplicateBtn) duplicateBtn.addEventListener('click', () => duplicateAsset(asset));

        const deleteBtn = document.getElementById('ggbDeleteBtn');
        if (deleteBtn) deleteBtn.addEventListener('click', () => deleteAsset(asset));
    }

    // ---------------- 检查器（标签 / 版本 / 关联题目） ----------------

    function renderInspector() {
        const tagsBox = document.getElementById('ggbInspectorTags');
        const versionsBox = document.getElementById('ggbInspectorVersions');
        const linksBox = document.getElementById('ggbInspectorLinks');
        const detail = GgbStore.detail;
        if (!detail) {
            if (tagsBox) tagsBox.innerHTML = '';
            if (versionsBox) versionsBox.innerHTML = '';
            if (linksBox) linksBox.innerHTML = '';
            return;
        }
        const asset = detail.asset || {};
        const tags = detail.tags || [];
        const versions = detail.versions || [];
        const links = detail.links || [];

        if (tagsBox) {
            tagsBox.innerHTML = `
                <h3 class="ggb-inspector-title">标签</h3>
                <div class="ggb-tag-list">
                    ${tags.length
                        ? tags.map((tag) => {
                            const code = typeof tag === 'string' ? tag : (tag.tag_code || tag.code || tag.label || '');
                            const dim = typeof tag === 'string' ? '' : (tag.tag_dim || tag.dim || '');
                            return `<span class="ggb-tag-chip">${escapeText(code)}${dim ? `<em>${escapeText(dim)}</em>` : ''}</span>`;
                        }).join('')
                        : '<span class="ggb-inspector-empty">还没有标签</span>'}
                </div>
                <button type="button" class="glass-btn" id="ggbEditTagsBtn"><i class="fa-solid fa-tags" aria-hidden="true"></i><span>编辑标签</span></button>`;
            const tagBtn = tagsBox.querySelector('#ggbEditTagsBtn');
            if (tagBtn) tagBtn.addEventListener('click', () => editTags(asset, tags));
        }

        if (versionsBox) {
            versionsBox.innerHTML = `
                <h3 class="ggb-inspector-title">版本（${versions.length}）</h3>
                <div class="ggb-version-list">
                    ${versions.length ? versions.map(version => {
                        const no = version.version_no || version.no || version.id;
                        const current = no === asset.current_version_no;
                        return `
                        <div class="ggb-version-item ${current ? 'is-current' : ''}">
                            <span class="ggb-version-no">v${escapeText(no)}${current ? ' · 当前' : ''}</span>
                            <span class="ggb-version-note">${escapeText(version.note || formatTime(version.created_at))}</span>
                            <span class="ggb-version-actions">
                                <button type="button" class="ggb-icon-btn" data-ggb-version-download="${escapeAttribute(no)}" title="下载该版本"><i class="fa-solid fa-download" aria-hidden="true"></i></button>
                                ${current ? '' : `<button type="button" class="ggb-icon-btn" data-ggb-version-restore="${escapeAttribute(no)}" title="回滚到该版本"><i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i></button>`}
                                ${versions.length > 1 ? `<button type="button" class="ggb-icon-btn ggb-icon-danger" data-ggb-version-delete="${escapeAttribute(no)}" title="删除该版本"><i class="fa-solid fa-trash-can" aria-hidden="true"></i></button>` : ''}
                            </span>
                        </div>`;
                    }).join('') : '<span class="ggb-inspector-empty">还没有版本记录</span>'}
                </div>`;
            versionsBox.querySelectorAll('[data-ggb-version-download]').forEach(button => {
                button.addEventListener('click', () => {
                    const no = button.dataset.ggbVersionDownload;
                    triggerDownload(`/api/ggb-assets/${asset.id}/versions/${no}/file`, `${asset.title || '演示'}-v${no}.ggb`);
                });
            });
            versionsBox.querySelectorAll('[data-ggb-version-restore]').forEach(button => {
                button.addEventListener('click', async () => {
                    const no = button.dataset.ggbVersionRestore;
                    if (!confirm(`确定回滚到 v${no} 吗？当前版本会保留，回滚会生成一个新版本。`)) return;
                    try {
                        await api(`/api/ggb-assets/${asset.id}/versions/${no}/restore`, { method: 'POST', json: {} });
                        toast(`已回滚到 v${no}`, 'success');
                        await loadAssetDetail(asset.id);
                    } catch (error) {
                        toast(error.message || '回滚失败', 'error');
                    }
                });
            });
            versionsBox.querySelectorAll('[data-ggb-version-delete]').forEach(button => {
                button.addEventListener('click', async () => {
                    const no = button.dataset.ggbVersionDelete;
                    if (!confirm(`确定删除 v${no} 吗？`)) return;
                    try {
                        await api(`/api/ggb-assets/${asset.id}/versions/${no}`, { method: 'DELETE' });
                        toast('版本已删除', 'success');
                        await loadAssetDetail(asset.id);
                    } catch (error) {
                        toast(error.message || '删除版本失败', 'error');
                    }
                });
            });
        }

        if (linksBox) {
            linksBox.innerHTML = `
                <h3 class="ggb-inspector-title">关联题目（${links.length}）</h3>
                <div class="ggb-link-list">
                    ${links.length ? links.map(link => `
                        <div class="ggb-link-item">
                            <span class="ggb-link-code">${escapeText(linkLabel(link.link_code))}</span>
                            <span class="ggb-link-question">#${escapeText(link.question_id)}${link.is_default ? ' · 默认' : ''}</span>
                            <span class="ggb-link-note">${escapeText(link.note || '')}</span>
                        </div>`).join('') : '<span class="ggb-inspector-empty">该资源尚未被任何题目关联</span>'}
                </div>`;
        }
    }

    // ---------------- 上传 / 编辑 / 版本 / 标签 / 删除 ----------------

    function openUploadModal() {
        const dynamicTypes = dynamicTypeOptions();
        const modal = ensureModalShell('ggbUploadModal', '上传 .ggb 动态演示', `
            <div class="ggb-form-grid">
                <label class="ggb-form-field ggb-form-full">
                    <span>选择 .ggb 文件 <em class="ggb-required">*</em></span>
                    <input id="ggbUploadFile" type="file" accept=".ggb,application/octet-stream" class="glass-input">
                </label>
                <label class="ggb-form-field ggb-form-full">
                    <span>标题 <em class="ggb-required">*</em></span>
                    <input id="ggbUploadTitle" type="text" class="glass-input" maxlength="200" placeholder="选择文件后自动填写，可修改">
                </label>
                <label class="ggb-form-field ggb-form-full">
                    <span>说明</span>
                    <textarea id="ggbUploadDesc" class="glass-input" rows="2" placeholder="可选"></textarea>
                </label>
                <label class="ggb-form-field">
                    <span>预览图</span>
                    <input id="ggbUploadPreview" type="file" accept="image/png,image/jpeg,image/webp,image/gif" class="glass-input">
                </label>
                <label class="ggb-form-field">
                    <span>演示类型</span>
                    <select id="ggbUploadDynamicType" class="glass-select">
                        <option value="">不指定</option>
                        ${dynamicTypes.map(type => `<option value="${escapeAttribute(type.code)}">${escapeText(type.label)}</option>`).join('')}
                    </select>
                </label>
                <label class="ggb-form-field ggb-form-full">
                    <span>标签（逗号分隔）</span>
                    <input id="ggbUploadTags" type="text" class="glass-input" placeholder="例如：二次函数, 图像变换">
                </label>
            </div>
            <p class="ggb-form-hint">标题默认取文件名（去掉 .ggb 后缀），教师文件名形如 2.5.1例3教材P99T13，无需手打。</p>`, `
            <button type="button" class="glass-btn" data-ggb-modal-close="ggbUploadModal">取消</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="ggbUploadSubmitBtn">上传</button>`);
        openModal('ggbUploadModal');

        const fileInput = modal.querySelector('#ggbUploadFile');
        const titleInput = modal.querySelector('#ggbUploadTitle');
        fileInput.addEventListener('change', () => {
            const file = fileInput.files && fileInput.files[0];
            if (!file) return;
            // 仅用文件名预填标题；不从文件名推断章节码（册别会误导自动推断）。
            titleInput.value = file.name.replace(/\.ggb$/i, '');
        });

        modal.querySelector('#ggbUploadSubmitBtn').addEventListener('click', async () => {
            const file = fileInput.files && fileInput.files[0];
            const title = titleInput.value.trim();
            if (!file) { toast('请先选择 .ggb 文件', 'info'); return; }
            if (!title) { toast('请填写标题', 'info'); return; }
            const formData = new FormData();
            formData.append('file', file);
            formData.append('title', title);
            formData.append('description', modal.querySelector('#ggbUploadDesc').value);
            const previewFile = modal.querySelector('#ggbUploadPreview').files[0];
            if (previewFile) formData.append('preview_image', previewFile);
            const dynamicType = modal.querySelector('#ggbUploadDynamicType').value;
            if (dynamicType) formData.append('dynamic_type', dynamicType);
            const tags = modal.querySelector('#ggbUploadTags').value.split(',').map(s => s.trim()).filter(Boolean);
            if (tags.length) formData.append('tags_json', JSON.stringify(tags));
            const submitBtn = modal.querySelector('#ggbUploadSubmitBtn');
            submitBtn.disabled = true;
            try {
                const result = await apiForm('/api/ggb-assets/upload', formData);
                toast('上传成功', 'success');
                closeModal('ggbUploadModal');
                await loadAssetList();
                const newId = result.data && (result.data.id || (result.data.asset && result.data.asset.id));
                if (newId) await selectAsset(newId);
            } catch (error) {
                toast(error.message || '上传失败', 'error');
            } finally {
                submitBtn.disabled = false;
            }
        });
    }

    // 在线编辑（V2.0）：可编辑 applet + multipart 替换当前文件。本次先接一半（保存回写）。
    function openOnlineEditor(asset) {
        if (!asset || !asset.id) return;
        const modal = ensureModalShell('ggbOnlineEditModal', `在线编辑 · ${asset.title || ''}`, `
            <div class="ggb-embed-panel">
                <div class="ggb-embed-host" id="ggbOnlineEditHost" role="region" aria-label="GeoGebra 在线编辑"></div>
                ${attributionHtml()}
            </div>
            <p class="ggb-form-hint">编辑后点击「保存为新文件」会把当前画布导出为 .ggb 并替换当前文件（后端替换接口就绪后生效）。</p>`, `
            <button type="button" class="glass-btn" data-ggb-modal-close="ggbOnlineEditModal">关闭</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="ggbOnlineEditSaveBtn">保存为新文件</button>`);
        openModal('ggbOnlineEditModal');
        const host = modal.querySelector('#ggbOnlineEditHost');
        host.innerHTML = stateHtml('loading', '正在加载 GeoGebra 编辑器');
        // 可编辑 applet（showToolBar / showAlgebraInput 等全开）。
        GgbEmbed.mount({ host, assetId: asset.id, editable: true });

        modal.querySelector('#ggbOnlineEditSaveBtn').addEventListener('click', async () => {
            const exportUrl = window.__ggbExportBase64;
            if (typeof exportUrl !== 'function') {
                toast('在线编辑保存需要 GeoGebra 导出支持，当前版本暂不可用。', 'info');
                return;
            }
            try {
                const base64 = await exportUrl();
                if (!base64) { toast('画布为空，无法保存', 'info'); return; }
                const binary = window.atob(base64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
                const blob = new Blob([bytes], { type: 'application/octet-stream' });
                const formData = new FormData();
                formData.append('file', blob, `${asset.title || 'demo'}.ggb`);
                await apiForm(`/api/ggb-assets/${asset.id}/file`, formData, 'POST');
                toast('已保存为新文件', 'success');
                closeModal('ggbOnlineEditModal');
                await loadAssetDetail(asset.id);
            } catch (error) {
                toast(error.message || '保存失败', 'error');
            }
        });
    }

    async function saveNewVersion(asset) {
        const note = prompt('本次修改说明（可选）');
        if (note === null) return;
        try {
            await api(`/api/ggb-assets/${asset.id}/versions`, { method: 'POST', json: { note: note || '' } });
            toast('已保存新版本', 'success');
            await loadAssetDetail(asset.id);
        } catch (error) {
            toast(error.message || '保存版本失败', 'error');
        }
    }

    async function duplicateAsset(asset) {
        try {
            const result = await api('/api/ggb-assets', {
                method: 'POST',
                json: { title: `${asset.title || '演示'} 副本`, description: asset.description || '' }
            });
            toast('已另存副本', 'success');
            await loadAssetList();
            const newId = result.data && (result.data.id || (result.data.asset && result.data.asset.id));
            if (newId) await selectAsset(newId);
        } catch (error) {
            toast(error.message || '另存副本失败', 'error');
        }
    }

    function editTags(asset, tags) {
        const current = (tags || []).map(tag => typeof tag === 'string' ? tag : (tag.tag_code || tag.code || tag.label || '')).filter(Boolean);
        const modal = ensureModalShell('ggbTagsModal', '编辑标签', `
            <label class="ggb-form-field ggb-form-full">
                <span>标签（逗号分隔，整体替换）</span>
                <input id="ggbTagsInput" type="text" class="glass-input" value="${escapeAttribute(current.join(', '))}">
            </label>`, `
            <button type="button" class="glass-btn" data-ggb-modal-close="ggbTagsModal">取消</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="ggbTagsSubmitBtn">保存</button>`);
        openModal('ggbTagsModal');
        modal.querySelector('#ggbTagsSubmitBtn').addEventListener('click', async () => {
            const entries = modal.querySelector('#ggbTagsInput').value.split(',').map(s => s.trim()).filter(Boolean);
            const payload = entries.map(tag => ({ tag_code: tag, tag_dim: 'custom' }));
            try {
                await api(`/api/ggb-assets/${asset.id}/tags`, { method: 'PUT', json: { tags: payload } });
                toast('标签已保存', 'success');
                closeModal('ggbTagsModal');
                await loadAssetDetail(asset.id);
            } catch (error) {
                toast(error.message || '保存标签失败', 'error');
            }
        });
    }

    async function deleteAsset(asset) {
        if (!asset || !asset.id) return;
        if (!confirm(`确定删除演示资源「${asset.title || ''}」吗？`)) return;
        try {
            await api(`/api/ggb-assets/${asset.id}`, { method: 'DELETE' });
            toast('演示资源已删除', 'success');
            GgbStore.currentAssetId = null;
            GgbStore.detail = null;
            GgbEmbed.unmount();
            renderDetailPanel();
            renderInspector();
            await loadAssetList();
        } catch (error) {
            // 有关联题且未 force → 409，询问是否强制删除。
            if (error.status === 409) {
                if (!confirm('该资源已被题目关联，仍要强制删除吗？关联关系会一并解除。')) return;
                try {
                    await api(`/api/ggb-assets/${asset.id}?force=true`, { method: 'DELETE' });
                    toast('演示资源已强制删除', 'success');
                    GgbStore.currentAssetId = null;
                    GgbStore.detail = null;
                    GgbEmbed.unmount();
                    renderDetailPanel();
                    renderInspector();
                    await loadAssetList();
                } catch (forcedError) {
                    toast(forcedError.message || '删除失败', 'error');
                }
                return;
            }
            toast(error.message || '删除失败', 'error');
        }
    }

    // ---------------- 题目侧「动态演示」面板（懒挂载，切走即销毁） ----------------

    function renderQuestionPanelBody() {
        const body = document.getElementById('ggbQuestionPanelBody');
        if (!body) return;
        const qid = GgbStore.currentQuestionId;
        if (!qid) {
            body.innerHTML = stateHtml('empty', '还没有选择题目', '先选择一道题目，再为它挂载动态演示。');
            return;
        }
        const links = GgbStore.questionLinks || [];
        body.innerHTML = `
            <div class="ggb-question-toolbar">
                <button type="button" class="glass-btn" id="ggbAddLinkBtn"><i class="fa-solid fa-link" aria-hidden="true"></i><span>添加关联演示</span></button>
                <button type="button" class="glass-btn" id="ggbGotoWorkspaceBtn"><i class="fa-solid fa-up-right-from-square" aria-hidden="true"></i><span>打开演示资源库</span></button>
            </div>
            <div class="ggb-question-links">
                ${links.length ? links.map(link => `
                    <div class="ggb-question-link ${link.is_default ? 'is-default' : ''}" data-ggb-link-id="${link.id}">
                        <span class="ggb-link-code">${escapeText(linkLabel(link.link_code))}</span>
                        <span class="ggb-link-name">${escapeText(link.asset_title || link.title || ('#' + link.ggb_id))}</span>
                        <span class="ggb-link-actions">
                            <button type="button" class="ggb-icon-btn" data-ggb-open-link="${link.id}" title="预览"><i class="fa-solid fa-eye" aria-hidden="true"></i></button>
                            <button type="button" class="ggb-icon-btn ggb-icon-danger" data-ggb-remove-link="${link.id}" title="解除关联"><i class="fa-solid fa-link-slash" aria-hidden="true"></i></button>
                        </span>
                    </div>`).join('') : '<span class="ggb-inspector-empty">该题还没有关联任何动态演示</span>'}
            </div>
            <div class="ggb-embed-panel ggb-question-embed">
                <div class="ggb-embed-host" id="ggbQuestionEmbedHost" role="region" aria-label="GeoGebra 动态演示"></div>
                ${attributionHtml()}
            </div>`;

        body.querySelector('#ggbAddLinkBtn').addEventListener('click', () => openLinkEditor(qid));
        body.querySelector('#ggbGotoWorkspaceBtn').addEventListener('click', () => {
            if (typeof window.selectWorkspace === 'function') window.selectWorkspace('ggb', '演示资源库');
        });
        body.querySelectorAll('[data-ggb-remove-link]').forEach(button => {
            button.addEventListener('click', async () => {
                if (!confirm('确定解除该演示与当前题目的关联吗？')) return;
                try {
                    await api(`/api/ggb-links/${button.dataset.ggbRemoveLink}`, { method: 'DELETE' });
                    toast('已解除关联', 'success');
                    await mountQuestionPanel(GgbStore.currentQuestionId);
                } catch (error) {
                    toast(error.message || '解除关联失败', 'error');
                }
            });
        });
        body.querySelectorAll('[data-ggb-open-link]').forEach(button => {
            button.addEventListener('click', () => {
                const link = (GgbStore.questionLinks || []).find(item => String(item.id) === String(button.dataset.ggbOpenLink));
                if (link) previewLink(link);
            });
        });

        // 懒挂载：只对默认（或首个）关联注入一个 applet。
        const target = links.find(link => link.is_default) || links[0];
        const host = document.getElementById('ggbQuestionEmbedHost');
        if (target && host) {
            host.innerHTML = stateHtml('loading', '正在加载 GeoGebra 组件');
            GgbEmbed.mount({ host, assetId: target.ggb_id });
        } else if (host) {
            host.innerHTML = stateHtml('empty', '暂无可预览的演示', '关联一个演示资源后即可在此预览。');
        }
    }

    function previewLink(link) {
        const host = document.getElementById('ggbQuestionEmbedHost');
        if (!host) return;
        host.innerHTML = stateHtml('loading', '正在加载 GeoGebra 组件');
        GgbEmbed.mount({ host, assetId: link.ggb_id });
    }

    async function mountQuestionPanel(questionId) {
        const qid = parseInt(questionId, 10);
        GgbStore.currentQuestionId = Number.isSafeInteger(qid) && qid > 0 ? qid : null;
        // 挂载前先销毁旧 applet。
        unmount();
        const body = document.getElementById('ggbQuestionPanelBody');
        if (!body) return;
        if (!GgbStore.currentQuestionId) {
            renderQuestionPanelBody();
            return;
        }
        body.innerHTML = stateHtml('loading', '正在载入关联演示');
        try {
            const result = await api(`/api/questions/${GgbStore.currentQuestionId}/ggb-links`);
            GgbStore.questionLinks = result.data || [];
        } catch (error) {
            debug.debug('GGB question links unavailable:', error && error.message);
            GgbStore.questionLinks = [];
        }
        renderQuestionPanelBody();
    }

    // 统一的销毁入口：委托 window.GgbEmbed.unmount()，供 import.js / editor.js 调用。
    function unmount() {
        if (window.GgbEmbed && typeof window.GgbEmbed.unmount === 'function') {
            window.GgbEmbed.unmount();
        }
    }

    // ---------------- 关联编辑器（题目侧） ----------------

    async function openLinkEditor(questionId) {
        const qid = parseInt(questionId, 10);
        if (!Number.isSafeInteger(qid) || qid <= 0) {
            toast('请先选择一道题目', 'info');
            return;
        }
        await loadConfig();
        let assets = GgbStore.assets;
        if (!assets.length) {
            try {
                const result = await api('/api/ggb-assets?page=1&page_size=200');
                const data = result.data;
                assets = Array.isArray(data) ? data : ((data && data.items) || []);
            } catch (error) {
                assets = [];
            }
        }
        const codes = linkCodes();
        const modal = ensureModalShell('ggbLinkModal', '关联动态演示', `
            <div class="ggb-form-grid">
                <label class="ggb-form-field ggb-form-full">
                    <span>演示资源 <em class="ggb-required">*</em></span>
                    <select id="ggbLinkAssetSelect" class="glass-select">
                        <option value="">请选择</option>
                        ${assets.map(asset => `<option value="${asset.id}">${escapeText(asset.title || ('#' + asset.id))}</option>`).join('')}
                    </select>
                </label>
                <label class="ggb-form-field">
                    <span>关联类型</span>
                    <select id="ggbLinkCodeSelect" class="glass-select">
                        ${codes.map(code => `<option value="${escapeAttribute(code)}">${escapeText(linkLabel(code))}</option>`).join('')}
                    </select>
                </label>
                <label class="ggb-form-field">
                    <span>自定义标签（可选）</span>
                    <input id="ggbLinkLabelInput" type="text" class="glass-input" maxlength="50" placeholder="留空则用预设类型名">
                </label>
                <label class="ggb-form-field ggb-form-full">
                    <span>备注</span>
                    <textarea id="ggbLinkNoteInput" class="glass-input" rows="2" placeholder="可选"></textarea>
                </label>
                <label class="ggb-form-field ggb-form-checkbox">
                    <input id="ggbLinkDefaultInput" type="checkbox">
                    <span>设为默认演示</span>
                </label>
            </div>`, `
            <button type="button" class="glass-btn" data-ggb-modal-close="ggbLinkModal">取消</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="ggbLinkSubmitBtn">建立关联</button>`);
        openModal('ggbLinkModal');
        modal.querySelector('#ggbLinkSubmitBtn').addEventListener('click', async () => {
            const ggbId = parseInt(modal.querySelector('#ggbLinkAssetSelect').value, 10);
            if (!ggbId) { toast('请选择演示资源', 'info'); return; }
            const payload = {
                ggb_id: ggbId,
                link_code: modal.querySelector('#ggbLinkCodeSelect').value,
                is_default: modal.querySelector('#ggbLinkDefaultInput').checked
            };
            const label = modal.querySelector('#ggbLinkLabelInput').value.trim();
            if (label) payload.link_label = label;
            const note = modal.querySelector('#ggbLinkNoteInput').value.trim();
            if (note) payload.note = note;
            try {
                await api(`/api/questions/${qid}/ggb-links`, { method: 'POST', json: payload });
                toast('已建立关联', 'success');
                closeModal('ggbLinkModal');
                await mountQuestionPanel(qid);
            } catch (error) {
                if (error.status === 409) toast('该题已关联此演示', 'info');
                else toast(error.message || '建立关联失败', 'error');
            }
        });
    }

    // ---------------- 入口渲染 ----------------

    function setGgbMobilePane(pane) {
        const body = document.querySelector('.ggb-workspace-body');
        if (!body) return;
        const target = ['list', 'detail', 'inspector'].includes(pane) ? pane : 'list';
        body.dataset.mobilePane = target;
        document.querySelectorAll('[data-ggb-pane]').forEach(button => {
            button.setAttribute('aria-selected', String(button.dataset.ggbPane === target));
        });
    }

    async function renderGgbWorkspace() {
        debug.debug('renderGgbWorkspace');
        await Promise.all([loadConfig(), loadAssetList()]);
        if (GgbStore.currentAssetId) {
            const exists = GgbStore.assets.some(asset => asset.id === GgbStore.currentAssetId);
            if (exists) await loadAssetDetail(GgbStore.currentAssetId);
            else {
                GgbStore.currentAssetId = null;
                GgbStore.detail = null;
                GgbEmbed.unmount();
                renderDetailPanel();
                renderInspector();
            }
        } else {
            renderDetailPanel();
            renderInspector();
        }
    }

    // ---------------- 命名空间导出（已冻结，测试会断言） ----------------

    window.GgbPanel = {
        render: renderGgbWorkspace,
        renderAssetCard,
        mountQuestionPanel,
        unmount,
        openUploadModal,
        openLinkEditor,
        downloadAsset,
        selectAsset,
        loadAssetList,
        setMobilePane: setGgbMobilePane
    };
    window.renderGgbWorkspace = renderGgbWorkspace;
    window.setGgbMobilePane = setGgbMobilePane;

    debug.debug('ggb module loaded');
})();
