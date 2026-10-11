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
            activePanel.remove();
            activePanel = null;
            activeInput = null;
        }
        document.removeEventListener('mousedown', onGlobalMousedown, true);
    }

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
        panel.className = 'tag-memory-panel absolute z-40 mt-1 w-full rounded-xl border border-slate-200 bg-white shadow-lg text-xs overflow-hidden';
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

        // 面板需要相对输入框定位：优先挂在输入框所在的 relative 容器内。
        const host = input.parentElement && input.parentElement.classList.contains('relative')
            ? input.parentElement
            : (input.parentElement || document.body);
        host.appendChild(panel);
        panel.style.position = 'absolute';
        panel.style.left = '0';
        panel.style.top = '100%';
        panel.style.marginTop = '4px';

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
