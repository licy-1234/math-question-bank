/**
 * filter-history.js — 题库管理 / 智能组卷 中「填空式筛选」的历史记忆组件。
 *
 * 背景：微观方法、来源、标签这几类取值是自由文本，做成下拉会随库增长越来越难用，
 * 改成填空搜索后就需要「记住历史输入并支持快速选择」。本组件用 localStorage 按
 * 字段维度（historyKey）保存最近输入（MRU，最多 MAX_ITEMS 条），聚焦/输入时弹出
 * 候选面板，点击即回填并触发回调（通常是重新拉取题目）。
 *
 * 对外接口：
 *   window.MathBankFilterHistory.attach(inputEl, options?)
 *     options = {
 *       historyKey: 'bank_micro' | ...,   // 缺省取 input.dataset.filterHistory
 *       onSelect: (value) => void,        // 缺省取 input.dataset.filterOnSelect 指向的全局函数
 *       record: true                      // 是否在本输入 committed 时记录（默认 true）
 *     }
 *   window.MathBankFilterHistory.record(historyKey, value)
 *   window.MathBankFilterHistory.list(historyKey)
 *   window.MathBankFilterHistory.clear(historyKey)
 */
(function () {
    'use strict';

    const STORAGE_PREFIX = 'mathbank.filterHistory.';
    const MAX_ITEMS = 24;

    function escapeHtml(value) {
        if (window.MathBankSafe && typeof window.MathBankSafe.escapeText === 'function') {
            return window.MathBankSafe.escapeText(value);
        }
        return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    function storageKey(historyKey) {
        return STORAGE_PREFIX + String(historyKey || 'default');
    }

    function list(historyKey) {
        try {
            const raw = localStorage.getItem(storageKey(historyKey));
            const items = raw ? JSON.parse(raw) : [];
            return Array.isArray(items) ? items.filter((item) => typeof item === 'string' && item.trim()) : [];
        } catch (error) {
            return [];
        }
    }

    function write(historyKey, items) {
        try {
            localStorage.setItem(storageKey(historyKey), JSON.stringify(items.slice(0, MAX_ITEMS)));
        } catch (error) {
            /* localStorage 不可用时静默降级为不记忆 */
        }
    }

    function record(historyKey, value) {
        const clean = String(value || '').trim();
        if (!clean || clean.length > 80) return;
        const items = list(historyKey).filter((item) => item !== clean);
        items.unshift(clean);
        write(historyKey, items);
    }

    function remove(historyKey, value) {
        write(historyKey, list(historyKey).filter((item) => item !== value));
    }

    function clear(historyKey) {
        write(historyKey, []);
    }

    // 面板是单例：任何时刻只存在一个输入的下拉，避免多个 review 面板互相遮挡。
    let activePanel = null;
    let activeInput = null;

    function closePanel() {
        if (activePanel) {
            if (window.MathBankFloatingPanel) window.MathBankFloatingPanel.unmount(activePanel);
            activePanel.remove();
            activePanel = null;
            activeInput = null;
        }
        document.removeEventListener('mousedown', onGlobalMousedown, true);
    }

    // -------------------------------------------------------------------------
    // 公共浮动面板定位工具（filter-history.js 与 tag-memory.js 共用；本文件在
    // index.html:2473 先于 tag-memory.js 加载，所以由这里对外暴露）。
    //
    // 为什么必须存在（三处硬伤，逐条对应「记忆面板 hover 导致整页错乱」）：
    //   1) 旧实现把面板 append 到「输入框所在的 relative 容器」或 document.body，
    //      定位/层级完全交给 Tailwind 的 absolute / mt-1 / z-40 / w-full 等工具类。
    //      本项目 Tailwind 用的是 Play CDN（static/index.html 引入
    //      /static/lib/tailwind/tailwind.min.js），样式是**运行时异步编译**的：
    //      节点入 DOM 的那一刻这些类还没有样式，面板会以普通块级元素参与布局——
    //      挂在筛选行里会把 .bank-filter-row 撑高（用户看到的「筛选条重叠行」），
    //      挂到 body 上则变成 body 的在流 flex 子项（body 的 flex 来自
    //      static/index.html:71 的 <body class="... flex">，是 Tailwind 类，非 app.css），
    //      整页被挤变形（「元素错位/抖动」）。注：body 本身在 app.css:1084 为
    //      overflow:hidden，页面并不滚动，滚动的其实是内部容器——因此面板改用
    //      position:fixed 挂在视口坐标上，并用 capture 监听捕获任意祖先容器的滚动。
    //      为此 position/left/top/width/z-index 一律改为内联样式，在第一帧就写死，
    //      不再依赖异步补样式。
    //   2) 旧 tag-memory 用 getBoundingClientRect()+scrollX/scrollY 一次性算坐标、
    //      宽度取 max(rect.width, 260)，「来源」在最右列时面板会越过视口右缘被裁掉。
    //      这里改为 fixed + 四边钳制（留 SAFE 边距），永不越界，也撑不出横向滚动条。
    //   3) 编辑弹窗 .question-editor-modal-body 是 overflow-y:auto 的滚动容器，
    //      页面本身也有滚动；旧实现坐标只算一次，滚动/resize 后面板就残留在错位处。
    //      这里监听 scroll（capture 阶段，能收到任意祖先滚动容器的滚动）与 resize，
    //      要么重算跟随，要么在锚点滚出视口时立刻关闭。
    //
    // 面板统一挂到 document.body 并用 position:fixed：既脱离任何祖先的 overflow
    // 裁剪与滚动坐标系（body/html 上没有 transform/filter，fixed 即视口坐标），
    // 又不会成为 body 的 flex 子项。
    // -------------------------------------------------------------------------
    const FLOAT = {
        SAFE: 8,        // 距视口四边的安全留白
        GAP: 4,         // 面板与输入框之间的间距（取代原来的 mt-1）
        MIN_WIDTH: 200, // 窄输入框上的最小可读宽度
        MAX_WIDTH: 448, // 对应原来的 max-w-md
        MAX_HEIGHT: 320 // 再高就内部滚动，绝不越出视口
    };

    function floatViewport() {
        // 用 clientWidth/clientHeight（不含滚动条）作为布局视口，fallback 到 innerWidth。
        const doc = document.documentElement;
        return {
            width: doc.clientWidth || window.innerWidth || 0,
            height: doc.clientHeight || window.innerHeight || 0
        };
    }

    /**
     * 按视口钳制重算面板位置。返回 false 表示锚点输入框已不在视口内，
     * 调用方应当立刻关闭面板，绝不让面板残留在错位处。
     */
    function floatReposition(panel, input) {
        if (!panel || !input || !panel.isConnected || !input.isConnected) return false;
        const rect = input.getBoundingClientRect();
        const view = floatViewport();
        if (!rect.width && !rect.height) return false;
        if (rect.bottom <= 0 || rect.top >= view.height) return false;
        if (rect.right <= 0 || rect.left >= view.width) return false;

        // 宽度：跟随输入框，但不窄于 MIN_WIDTH、不宽于 MAX_WIDTH、不宽于视口可用宽度。
        const usable = Math.max(view.width - FLOAT.SAFE * 2, FLOAT.MIN_WIDTH);
        const width = Math.min(Math.max(rect.width, FLOAT.MIN_WIDTH), FLOAT.MAX_WIDTH, usable);
        panel.style.width = Math.round(width) + 'px';

        // 水平：默认与输入框左对齐，越界则贴回视口内。
        let left = rect.left;
        if (left + width > view.width - FLOAT.SAFE) left = view.width - FLOAT.SAFE - width;
        if (left < FLOAT.SAFE) left = FLOAT.SAFE;
        panel.style.left = Math.round(left) + 'px';

        // 垂直：优先放在输入框下方；下方放不下且上方更宽裕时翻到上方。
        const wanted = Math.min(panel.scrollHeight || FLOAT.MAX_HEIGHT, FLOAT.MAX_HEIGHT);
        const below = view.height - rect.bottom - FLOAT.GAP - FLOAT.SAFE;
        const above = rect.top - FLOAT.GAP - FLOAT.SAFE;
        let top;
        let room;
        if (below >= Math.min(wanted, 120) || below >= above) {
            top = rect.bottom + FLOAT.GAP;
            room = below;
        } else {
            room = above;
            top = rect.top - FLOAT.GAP - Math.min(wanted, Math.max(room, 0));
        }
        // 高度同样以可用空间为准：可用空间不足 96px 时不要硬撑到 96，
        // 否则下面那层钳制会把 top 往上抬，面板反过来盖住输入框。
        const roomSafe = Math.max(room, 0);
        const height = Math.max(Math.min(wanted, roomSafe), Math.min(96, roomSafe));
        if (top < FLOAT.SAFE) top = FLOAT.SAFE;
        if (top + height > view.height - FLOAT.SAFE) {
            top = Math.max(FLOAT.SAFE, view.height - FLOAT.SAFE - height);
        }
        panel.style.top = Math.round(top) + 'px';
        panel.style.maxHeight = Math.round(height) + 'px';
        return true;
    }

    let trackedPanel = null;
    let trackedInput = null;
    let trackedDismiss = null;
    let trackedFrame = 0;

    function floatUnmount(panel) {
        if (trackedPanel && panel && trackedPanel !== panel) return;
        if (trackedFrame) {
            cancelAnimationFrame(trackedFrame);
            trackedFrame = 0;
        }
        trackedPanel = null;
        trackedInput = null;
        trackedDismiss = null;
        window.removeEventListener('scroll', onViewportChanged, true);
        window.removeEventListener('resize', onViewportChanged);
    }

    function onViewportChanged(event) {
        if (!trackedPanel) return;
        // 面板自身的内部滚动不触发重算，否则列表滚动时面板会自己跳动。
        // nodeType 判断不能省：resize 事件的 event.target 是 window（不是 Node），
        // 直接喂给 Node.contains() 会抛
        // "Failed to execute 'contains' on 'Node': parameter 1 is not of type 'Node'"，
        // 异常发生在 rAF 之前会让整个 resize 跟随/关闭逻辑静默失效。
        if (event && event.target && event.target.nodeType && trackedPanel.contains(event.target)) return;
        if (trackedFrame) return;
        trackedFrame = requestAnimationFrame(() => {
            trackedFrame = 0;
            if (!trackedPanel) return;
            if (floatReposition(trackedPanel, trackedInput)) return;
            const dismiss = trackedDismiss;
            floatUnmount(trackedPanel);
            if (typeof dismiss === 'function') dismiss();
        });
    }

    /** 挂到 body、写入定位内联样式、注册滚动/resize 跟随，并返回首帧定位结果。 */
    function floatMount(panel, input, onDismiss) {
        floatUnmount();
        panel.style.position = 'fixed';
        panel.style.margin = '0';
        panel.style.boxSizing = 'border-box';
        panel.style.maxWidth = 'none';
        // 60 > 编辑弹窗 #editorSection 的 49（app.css），否则面板会被弹窗遮罩压住。
        panel.style.zIndex = '60';
        panel.style.overflowY = 'auto';
        document.body.appendChild(panel);
        trackedPanel = panel;
        trackedInput = input;
        trackedDismiss = typeof onDismiss === 'function' ? onDismiss : null;
        window.addEventListener('scroll', onViewportChanged, true);
        window.addEventListener('resize', onViewportChanged);
        return floatReposition(panel, input);
    }

    window.MathBankFloatingPanel = {
        mount: floatMount,
        reposition: floatReposition,
        unmount: floatUnmount
    };

    function onGlobalMousedown(event) {
        if (activePanel && !activePanel.contains(event.target) && event.target !== activeInput) {
            closePanel();
        }
    }

    function resolveCallback(input, options) {
        if (typeof options.onSelect === 'function') return options.onSelect;
        const name = input.dataset.filterOnSelect;
        if (name && typeof window[name] === 'function') return window[name];
        return null;
    }

    function renderPanel(input, options) {
        const historyKey = options.historyKey;
        const query = String(input.value || '').trim().toLowerCase();
        const items = list(historyKey).filter(
            (item) => !query || item.toLowerCase().indexOf(query) !== -1
        );

        closePanel();
        if (!items.length) return;

        const panel = document.createElement('div');
        // 定位/尺寸/层级已全部由 MathBankFloatingPanel 以内联样式接管，className 只留
        // 外观类——否则 absolute / mt-1 / w-full / z-40 这些类在 Play CDN 异步编译生效前
        // 不存在，面板会先以普通块级元素撑坏所在行。
        panel.className = 'tag-memory-panel rounded-xl border border-slate-200 bg-white shadow-lg text-xs';
        let html = '<div class="px-3 pt-2 pb-1 flex items-center justify-between">'
            + '<span class="text-[10px] font-semibold text-slate-400">历史输入（最近使用优先）</span>'
            + '<button type="button" data-action="clear-all" class="text-[10px] text-slate-400 hover:text-rose-500">清空历史</button>'
            + '</div>'
            + '<div class="max-h-52 overflow-y-auto pb-1">';
        items.forEach((item) => {
            html += '<div class="flex items-center gap-1.5 px-3 py-1.5 hover:bg-slate-50">';
            html += `<button type="button" data-action="pick" data-value="${escapeHtml(item)}" class="flex-1 min-w-0 text-left truncate text-slate-700">${escapeHtml(item)}</button>`;
            html += `<button type="button" data-action="delete" data-value="${escapeHtml(item)}" title="删除这条历史" class="shrink-0 text-slate-300 hover:text-rose-500 px-1"><i class="fa-solid fa-xmark text-[10px]"></i></button>`;
            html += '</div>';
        });
        html += '</div>';
        panel.innerHTML = html;

        panel.addEventListener('click', (event) => {
            const button = event.target.closest('button[data-action]');
            if (!button) return;
            const action = button.dataset.action;
            const value = button.dataset.value || '';
            if (action === 'pick') {
                input.value = value;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                record(historyKey, value);
                const callback = resolveCallback(input, options);
                if (callback) callback(value);
                closePanel();
            } else if (action === 'delete') {
                remove(historyKey, value);
                renderPanel(input, options);
            } else if (action === 'clear-all') {
                if (!window.confirm('确定清空这个筛选框的全部历史输入吗？')) return;
                clear(historyKey);
                closePanel();
            }
        });

        // 与 tag-memory 的面板互斥：两套面板任何时刻只留一个，避免相互叠压。
        if (window.MathBankTagMemory && typeof window.MathBankTagMemory.closePanel === 'function') {
            window.MathBankTagMemory.closePanel();
        }
        let placed = true;
        if (window.MathBankFloatingPanel) {
            placed = window.MathBankFloatingPanel.mount(panel, input, () => closePanel());
        } else {
            document.body.appendChild(panel);
        }
        // 锚点不在视口内（例如所在工作区刚被切走）：不留任何残留面板。
        if (!placed) {
            if (window.MathBankFloatingPanel) window.MathBankFloatingPanel.unmount(panel);
            panel.remove();
            return;
        }

        activePanel = panel;
        activeInput = input;
        document.addEventListener('mousedown', onGlobalMousedown, true);
    }

    function attach(input, options) {
        if (!input || input.dataset.filterHistoryBound === '1') return;
        options = options || {};
        const historyKey = options.historyKey || input.dataset.filterHistory || input.id || 'default';
        input.dataset.filterHistoryBound = '1';
        input.setAttribute('autocomplete', 'off');
        const bound = { historyKey, onSelect: options.onSelect, record: options.record !== false };

        input.addEventListener('focus', () => renderPanel(input, bound));
        input.addEventListener('click', () => {
            if (activeInput !== input) renderPanel(input, bound);
        });
        input.addEventListener('input', () => {
            if (activeInput === input) renderPanel(input, bound);
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') closePanel();
            if (event.key === 'Enter') {
                closePanel();
                if (bound.record) record(historyKey, input.value);
                const callback = resolveCallback(input, bound);
                if (callback) callback(input.value);
            }
        });
        // 失焦时记录（延迟到 click 判定之后再写，避免下拉被提前关掉）。
        input.addEventListener('blur', () => {
            if (bound.record) record(historyKey, input.value);
        });
    }

    function attachAll(root) {
        (root || document).querySelectorAll('input[data-filter-history]').forEach((input) => {
            attach(input);
        });
    }

    window.MathBankFilterHistory = { attach, attachAll, record, remove, list, clear, closePanel };
})();
