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
    // dim -> 面板内「过滤历史标签」框当前的关键字（面板重渲染后回填，避免过滤词丢失）
    let historyFilterWord = {};

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
            if (window.MathBankFloatingPanel) window.MathBankFloatingPanel.unmount(activePanel);
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
        // 定位/尺寸/层级已全部由 MathBankFloatingPanel 以内联样式接管，className 只留
        // 外观类——否则 absolute / mt-1 / w-full / max-w-md / z-40 这些类在 Play CDN
        // 异步编译生效前不存在，面板会先以普通块级元素成为 body（display:flex）的在流
        // 子项，把整页布局挤变形。
        panel.className = 'tag-memory-panel rounded-xl border border-slate-200 bg-white shadow-lg text-xs';
        let html = '';

        // 历史标签区。
        //   · sortByCount=true（微观解题方法）：始终渲染该区块，按使用次数降序，
        //     并额外提供一个独立的关键字过滤框；
        //   · 缺省（自定义标签）：维持原有的「最近使用优先」且仅非空时渲染的行为。
        const isCountSort = options.sortByCount === true;
        const memoryOrdered = isCountSort
            ? memory.slice().sort((a, b) => {
                const countA = Number(a && a.use_count) || 0;
                const countB = Number(b && b.use_count) || 0;
                if (countB !== countA) return countB - countA;
                // 次数相同按最近使用时间降序，保证顺序稳定可预期。
                return String((b && b.last_used_at) || '').localeCompare(String((a && a.last_used_at) || ''));
            })
            : memory;
        // 「上次使用」徽标改为按时间匹配：次数排序下第一条已不再等于最近使用的那条。
        const latestTime = memory.reduce((max, entry) => {
            const time = String((entry && entry.last_used_at) || '');
            return time > max ? time : max;
        }, '');

        // 历史标签行：过滤框变化时只重建这一段，过滤框本身因此不会失焦。
        const historyRowsHtml = () => {
            const word = String(historyFilterWord[dim] || '').trim().toLowerCase();
            let rows = '';
            memoryOrdered.forEach((entry) => {
                const tag = (entry && entry.tag) || '';
                if (!tag) return;
                const lowTag = tag.toLowerCase();
                if (query && lowTag.indexOf(query) === -1) return;
                if (word && lowTag.indexOf(word) === -1) return;
                const isSelected = queryParts.indexOf(lowTag) !== -1;
                const isMarked = lastUsedTag[dim]
                    ? lastUsedTag[dim] === tag
                    : String((entry && entry.last_used_at) || '') === latestTime;
                rows += `<div class="flex items-center gap-1.5 px-3 py-1.5 hover:bg-slate-50 ${isSelected ? 'opacity-50' : ''}">`;
                rows += `<button type="button" data-action="pick" data-tag="${escapeHtml(tag)}" class="flex-1 min-w-0 text-left truncate">`;
                rows += `<span class="text-slate-700">${escapeHtml(tag)}</span>`;
                if (isMarked) rows += '<span class="ml-1 text-[9px] font-bold text-brand-600 bg-brand-50 border border-brand-200 rounded px-1 py-0.5 align-middle">上次使用</span>';
                rows += `<span class="ml-1 text-[9px] text-slate-400">${escapeHtml(Number(entry.use_count) || 1)}次</span>`;
                rows += '</button>';
                rows += `<button type="button" data-action="delete" data-tag="${escapeHtml(tag)}" title="删除这条记忆" class="shrink-0 text-slate-300 hover:text-rose-500 px-1"><i class="fa-solid fa-xmark text-[10px]"></i></button>`;
                rows += '</div>';
            });
            if (!rows) {
                // 有标签但被过滤掉 = 「没有匹配」；本来就没有 = 引导文案。
                const hasAnyBeforeFilter = memoryOrdered.some((entry) => {
                    const tag = String((entry && entry.tag) || '');
                    if (!tag) return false;
                    return !(query && tag.toLowerCase().indexOf(query) === -1);
                });
                rows = `<div class="px-3 py-3 text-[11px] text-slate-400">${hasAnyBeforeFilter ? '没有匹配的历史标签' : (options.emptyMemoryHint || '暂无历史标签。保存题目后，这里会记住你用过的标签。')}</div>`;
            }
            return rows;
        };

        if (isCountSort || memory.length) {
            html += '<div class="px-3 pt-2 pb-1 flex items-center justify-between">';
            html += `<span class="text-[10px] font-semibold text-slate-400">${isCountSort ? '历史标签（按使用次数优先）' : '记忆的标签（最近使用优先）'}</span>`;
            if (memory.length) {
                html += `<button type="button" data-action="clear-all" class="text-[10px] text-slate-400 hover:text-rose-500">清空记忆</button>`;
            }
            html += '</div>';
            if (isCountSort && memory.length) {
                html += '<div class="px-3 pb-1.5">';
                html += `<input type="text" class="tag-memory-filter w-full px-2 py-1 text-[11px] rounded-lg border border-slate-200 bg-white" placeholder="过滤历史标签…" autocomplete="off" value="${escapeHtml(historyFilterWord[dim] || '')}">`;
                html += '</div>';
            }
            html += `<div class="max-h-56 overflow-y-auto" data-role="history-list">${historyRowsHtml()}</div>`;
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

        // 面板内的关键字过滤框：只重建历史标签列表，不动输入框本身，因此不会失焦。
        const filterBox = panel.querySelector('.tag-memory-filter');
        if (filterBox) {
            filterBox.addEventListener('input', () => {
                historyFilterWord[dim] = filterBox.value || '';
                const list = panel.querySelector('[data-role="history-list"]');
                if (list) list.innerHTML = historyRowsHtml();
            });
            // Escape 先清空过滤词，再按一次才关闭面板（输入框主体仍是一次性关闭）。
            filterBox.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && filterBox.value) {
                    event.stopPropagation();
                    filterBox.value = '';
                    historyFilterWord[dim] = '';
                    const list = panel.querySelector('[data-role="history-list"]');
                    if (list) list.innerHTML = historyRowsHtml();
                }
            });
        }

        // 换面板前先撤掉旧面板（含滚动/resize 监听），避免 DOM 里残留多个面板。
        if (activePanel) {
            if (window.MathBankFloatingPanel) window.MathBankFloatingPanel.unmount(activePanel);
            activePanel.remove();
            activePanel = null;
        }
        // 与 filter-history 的面板互斥：两套面板任何时刻只留一个，避免相互叠压。
        if (window.MathBankFilterHistory && typeof window.MathBankFilterHistory.closePanel === 'function') {
            window.MathBankFilterHistory.closePanel();
        }

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
                    })
                    // 断网 / 后端报错时不要留 unhandled rejection，同时作废缓存，
                    // 下次聚焦会重新拉取，不会一直显示已删除的那条。
                    .catch(() => invalidate(dim));
            } else if (action === 'clear-all') {
                if (!window.confirm('确定清空该类型的全部标签记忆吗？（不影响已保存的题目）')) return;
                fetch(`/api/tag-memory?dim=${encodeURIComponent(dim)}&clear=true`, { method: 'DELETE' })
                    .then(() => {
                        invalidate(dim);
                        lastUsedTag[dim] = '';
                        return fetchMemory(dim, true).then(() => renderPanel(input, options));
                    })
                    .catch(() => invalidate(dim));
            }
        });

        // fixed + 视口钳制定位（详见 filter-history.js 里的 MathBankFloatingPanel）：
        // 不再用 getBoundingClientRect()+scrollX/scrollY 一次性算坐标，
        // 也就不会再出现「来源在最右列 → 面板越过视口右缘被裁掉 / 撑出横向滚动条」。
        let placed = true;
        if (window.MathBankFloatingPanel) {
            placed = window.MathBankFloatingPanel.mount(panel, input, () => closePanel());
        } else {
            // 兜底：公共工具缺失时至少保持 fixed 定位，绝不退化成参与布局的块级元素。
            const rect = input.getBoundingClientRect();
            panel.style.position = 'fixed';
            panel.style.zIndex = '60';
            panel.style.margin = '0';
            panel.style.boxSizing = 'border-box';
            document.body.appendChild(panel);
            panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - panel.offsetWidth - 8)) + 'px';
            panel.style.top = Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - panel.offsetHeight - 8)) + 'px';
        }
        // 锚点不在视口内（例如所在面板刚被切走）：不留任何残留面板。
        if (!placed) {
            if (window.MathBankFloatingPanel) window.MathBankFloatingPanel.unmount(panel);
            panel.remove();
            return;
        }

        activePanel = panel;
        activeInput = input;
        document.addEventListener('mousedown', onGlobalMousedown, true);
        window.addEventListener('blur', closePanel);
    }

    /**
     * 让浏览器彻底闭上嘴：Chrome / Edge 常常无视 autocomplete="off"，仍会在自由文本
     * 框上弹出「保存的信息」自动填充卡（本机要用答案我可以给你 mach!!!chrome!!!），
     * 一旦被手滑选中，老师手打的自定义标签就被整串替换掉了。
     *
     * 对策是组合拳：
     *   1. 常用 attribution 标记（各类密码管理器 / HTML 表单识别）；
     *   2. 输入框初始为 readonly —— 浏览器不会对只读字段弹自动填充，
     *      pointerdown / focus / keydown 任一触发时立刻解锁，用户完全无感。
     */
    function makeAutofillProof(input) {
        // 幂等守卫：editSource 这类输入框会先被业务代码调一次、再被
        // MathBankFilterHistory.attach 调一次。浏览器按「同一元素 + 同一监听器
        // 类型 + 同一 capture 标志」去重，但这里每次都是新的闭包，去重不了——
        // 重复注册 unlock/relock 会让 readonly 的解锁/重锁互相打架。
        if (!input || input.dataset.autofillProofBound === '1') return;
        input.dataset.autofillProofBound = '1';
        input.setAttribute('autocomplete', 'off');
        input.setAttribute('data-form-type', 'other');
        input.setAttribute('data-lpignore', 'true');
        input.setAttribute('data-1p-ignore', 'true');
        input.setAttribute('data-bwignore', 'true');
        // 初始只读：浏览器不会对 readonly 字段弹「保存的信息」自动填充卡。
        input.setAttribute('readonly', 'readonly');
        const unlock = () => input.removeAttribute('readonly');
        const relock = () => {
            // 失焦即重新上锁：浏览器只在字段「聚焦的瞬间」决定是否弹自动填充卡，
            // 保证下一次聚焦时依旧是 readonly，卡就永远弹不出来。
            if (document.activeElement !== input) input.setAttribute('readonly', 'readonly');
        };
        // 解锁时机有讲究：不能挂在 pointerdown / mousedown / focus 上——这些事件
        // 与 focus 同时或更早，先解锁就等于没防（上一版的教训）。click / keydown /
        // touchstart / mouseup / contextmenu 都能让用户正常开始输入与粘贴。
        input.addEventListener('click', unlock);
        input.addEventListener('keydown', unlock);
        input.addEventListener('touchstart', unlock, { passive: true });
        input.addEventListener('mouseup', unlock);
        input.addEventListener('contextmenu', unlock);
        input.addEventListener('blur', relock);
    }

    function attach(input, options) {
        if (!input || input.dataset.tagMemoryBound === '1') return;
        options = options || {};
        const dim = options.dim || 'custom';
        input.dataset.tagMemoryBound = '1';
        makeAutofillProof(input);
        input.dataset.tagMemoryDim = dim;

        input.addEventListener('focus', () => {
            fetchMemory(dim).then((items) => {
                // 请求返回前焦点可能已经移走、输入框也可能已被重渲染销毁
                // （例如组卷筛选行被整段重建）。此时再渲染就会把面板挂到已失效的
                // 输入框上，还会顺手 closePanel() 掉别处刚打开的面板。
                if (!input.isConnected || document.activeElement !== input) return;
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

    window.MathBankTagMemory = { attach, closePanel, notifySaved, invalidate, fetchMemory, makeAutofillProof };
})();
