/**
 * tag-memory.js — 自定义标签 / 微观解题方法 的「记忆复用」下拉组件。
 *
 * 解决的缺陷：此前这两类自由文本标签的记忆完全依赖浏览器自动填充
 * （Chrome 的「保存的信息」弹窗）——排序不可控、无法在应用内增删、
 * 换浏览器就丢。本组件把记忆收敛为应用级能力：
 *   · 后端 /api/tag-memory 记录每次保存（tag + last_used_at + use_count）
 *   · 输入框聚焦时弹出 MRU 面板：最近使用优先、「上次使用」徽标、使用次数
 *   · 输入即过滤（大小写不敏感的包含匹配）
 *   · 点击回填（追加为逗号分隔列表的一项）、单条 × 删除
 *
 * 对外接口：window.MathBankTagMemory.attach(inputEl, options)
 *   options: {
 *     dim: 'custom' | 'micro',
 *     getSuggestions: () => [{label, hint}]   // 可选：目录建议（微观方法参考目录）
 *   }
 */
(function () {
    'use strict';

    let cacheByDim = {};       // dim -> [{tag, last_used_at, use_count}]
    let activePanel = null;    // 当前打开的面板元素
    let activeInput = null;
    let lastUsedTag = {};      // dim -> 最近一次使用的 tag

    function escapeHtml(value) {
        if (window.MathBankSafe && typeof window.MathBankSafe.escapeText === 'function') {
            return window.MathBankSafe.escapeText(value);
        }
        return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    function fetchMemory(dim, force) {
        if (!force && cacheByDim[dim]) {
            return Promise.resolve(cacheByDim[dim]);
        }
        return fetch(`/api/tag-memory?dim=${encodeURIComponent(dim)}`)
            .then((res) => (res.ok ? res.json() : { items: [] }))
            .then((data) => {
                cacheByDim[dim] = Array.isArray(data.items) ? data.items : [];
                return cacheByDim[dim];
            })
            .catch(() => []);
    }

    function invalidate(dim) {
        delete cacheByDim[dim];
    }

    function closePanel() {
        if (activePanel) {
            activePanel.remove();
            activePanel = null;
            activeInput = null;
        }
        document.removeEventListener('mousedown', onGlobalMousedown, true);
        window.removeEventListener('blur', closePanel);
    }

    function onGlobalMousedown(event) {
        if (activePanel && !activePanel.contains(event.target) && event.target !== activeInput) {
            closePanel();
        }
    }

    // 把一个 tag 回填进输入框：追加为逗号分隔列表的一项（去重）。
    function appendTagToInput(input, tag) {
        const current = String(input.value || '');
        const parts = current.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
        if (parts.indexOf(tag) === -1) parts.push(tag);
        input.value = parts.join(',');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
        const end = input.value.length;
        try { input.setSelectionRange(end, end); } catch (e) { /* ignore */ }
    }

    function removeTagFromInput(input, tag) {
        const parts = String(input.value || '').split(/[,，]/)
            .map((item) => item.trim()).filter(Boolean);
        const next = parts.filter((item) => item !== tag);
        input.value = next.join(',');
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function currentInputTags(input) {
        return String(input.value || '').split(/[,，]/).map((item) => item.trim()).filter(Boolean);
    }

    function renderPanel(input, options) {
        const dim = options.dim;
        const query = String(input.value || '').trim().toLowerCase();
        const queryParts = currentInputTags(input).map((item) => item.toLowerCase());
        const memory = cacheByDim[dim] || [];
        const typedNew = queryParts.filter(
            (part) => !memory.some((entry) => entry.tag.toLowerCase() === part)
        );

        const suggestions = (options.getSuggestions ? options.getSuggestions() : [])
            .filter((item) => item && item.label)
            .filter((item) => !query || item.label.toLowerCase().indexOf(query) !== -1)
            .slice(0, 8);

        const panel = document.createElement('div');
        panel.className = 'tag-memory-panel absolute z-40 mt-1 w-full max-w-md rounded-xl border border-slate-200 bg-white shadow-lg text-xs overflow-hidden';
        let html = '';

        if (memory.length) {
            html += '<div class="px-3 pt-2 pb-1 flex items-center justify-between">';
            html += '<span class="text-[10px] font-semibold text-slate-400">记忆的标签（最近使用优先）</span>';
            html += `<button type="button" data-action="clear-all" class="text-[10px] text-slate-400 hover:text-rose-500">清空记忆</button>`;
            html += '</div>';
            html += '<div class="max-h-56 overflow-y-auto">';
            memory.forEach((entry, index) => {
                const tag = entry.tag || '';
                const lowTag = tag.toLowerCase();
                if (query && lowTag.indexOf(query) === -1) return;
                const isSelected = queryParts.indexOf(lowTag) !== -1;
                const isLastUsed = index === 0 && !lastUsedTag[dim];
                const isMarked = lastUsedTag[dim] === tag || isLastUsed;
                html += `<div class="flex items-center gap-1.5 px-3 py-1.5 hover:bg-slate-50 ${isSelected ? 'opacity-50' : ''}">`;
                html += `<button type="button" data-action="pick" data-tag="${escapeHtml(tag)}" class="flex-1 min-w-0 text-left truncate">`;
                html += `<span class="text-slate-700">${escapeHtml(tag)}</span>`;
                if (isMarked) html += '<span class="ml-1 text-[9px] font-bold text-brand-600 bg-brand-50 border border-brand-200 rounded px-1 py-0.5 align-middle">上次使用</span>';
                if ((entry.use_count || 0) > 1) html += `<span class="ml-1 text-[9px] text-slate-400">${escapeHtml(entry.use_count)}次</span>`;
                html += '</button>';
                html += `<button type="button" data-action="delete" data-tag="${escapeHtml(tag)}" title="删除这条记忆" class="shrink-0 text-slate-300 hover:text-rose-500 px-1"><i class="fa-solid fa-xmark text-[10px]"></i></button>`;
                html += '</div>';
            });
            html += '</div>';
        }

        if (suggestions.length) {
            html += `<div class="px-3 pt-2 pb-1 text-[10px] font-semibold text-slate-400">${options.suggestionTitle || '目录建议'}</div>`;
            html += '<div class="max-h-44 overflow-y-auto pb-1">';
            suggestions.forEach((item) => {
                html += `<button type="button" data-action="pick" data-tag="${escapeHtml(item.label)}" title="${escapeHtml(item.hint || item.label)}" class="block w-full text-left px-3 py-1.5 hover:bg-slate-50 truncate">`;
                html += `<span class="text-slate-600">${escapeHtml(item.label)}</span>`;
                if (item.hint) html += `<span class="ml-1 text-[10px] text-slate-400">${escapeHtml(item.hint)}</span>`;
                html += '</button>';
            });
            html += '</div>';
        }

        if (!html) {
            html = '<div class="px-3 py-3 text-[11px] text-slate-400">暂无记忆。保存题目后，这里会记住你用过的标签。</div>';
        } else if (typedNew.length) {
            html += `<div class="px-3 py-1.5 text-[10px] text-slate-400 border-t border-slate-100">保存后将记住：${escapeHtml(typedNew.join('、'))}</div>`;
        }

        panel.innerHTML = html;

        const inputRect = () => input.getBoundingClientRect();
        document.body.appendChild(panel);
        const rect = inputRect();
        panel.style.left = Math.round(rect.left + window.scrollX) + 'px';
        panel.style.top = Math.round(rect.bottom + window.scrollY) + 'px';
        panel.style.width = Math.max(rect.width, 260) + 'px';

        panel.addEventListener('click', (event) => {
            const button = event.target.closest('button[data-action]');
            if (!button) return;
            const action = button.dataset.action;
            const tag = button.dataset.tag || '';
            if (action === 'pick') {
                appendTagToInput(input, tag);
                renderPanel(input, options);
            } else if (action === 'delete') {
                fetch(`/api/tag-memory?dim=${encodeURIComponent(dim)}&tag=${encodeURIComponent(tag)}`, { method: 'DELETE' })
                    .then(() => {
                        invalidate(dim);
                        removeTagFromInput(input, tag);
                        return fetchMemory(dim, true).then(() => renderPanel(input, options));
                    });
            } else if (action === 'clear-all') {
                if (!window.confirm('确定清空该类型的全部标签记忆吗？（不影响已保存的题目）')) return;
                fetch(`/api/tag-memory?dim=${encodeURIComponent(dim)}&clear=true`, { method: 'DELETE' })
                    .then(() => {
                        invalidate(dim);
                        lastUsedTag[dim] = '';
                        return fetchMemory(dim, true).then(() => renderPanel(input, options));
                    });
            }
        });

        if (activePanel) activePanel.remove();
        activePanel = panel;
        activeInput = input;
        document.addEventListener('mousedown', onGlobalMousedown, true);
        window.addEventListener('blur', closePanel);
    }

    function attach(input, options) {
        if (!input || input.dataset.tagMemoryBound === '1') return;
        options = options || {};
        const dim = options.dim || 'custom';
        input.dataset.tagMemoryBound = '1';
        input.setAttribute('autocomplete', 'off');
        input.dataset.tagMemoryDim = dim;

        input.addEventListener('focus', () => {
            fetchMemory(dim).then((items) => {
                if (items.length) lastUsedTag[dim] = items[0].tag;
                closePanel();
                renderPanel(input, options);
            });
        });
        input.addEventListener('input', () => {
            if (activeInput === input) renderPanel(input, options);
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') closePanel();
        });
    }

    function notifySaved(dim, tags) {
        // 保存题目后调用：刷新记忆缓存并标记「上次使用」。
        if (!dim || !Array.isArray(tags) || !tags.length) return;
        invalidate(dim);
        lastUsedTag[dim] = tags[tags.length - 1];
        fetchMemory(dim, true);
    }

    window.MathBankTagMemory = { attach, closePanel, notifySaved, invalidate, fetchMemory };
})();
