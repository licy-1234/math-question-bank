// topic.js —— 专题工坊 IIFE 模块（照 qa.js / paper.js 先例）
// 必须在 paper.js 之后加载：selectWorkspace 的 topic 分支在 paper.js 中。
// 调试纪律：所有 verbose 日志走 window.MathBankDebug（localStorage mathbank_debug_logs 门控），
// 只有真实错误才 console.error。
(function () {
    'use strict';
    const debug = window.MathBankDebug;
    const safe = window.MathBankSafe;

    const TopicStore = {
        topics: [],
        currentTopicId: null,
        detail: null,           // {topic, groups, items, children, links, version_count}
        config: null,           // /api/config/topic-types
        viewMode: 'linear',
        selectedQuestionId: null,
        keyword: '',
        typeFilter: '',
        loadingList: false,
        loadingDetail: false,
        dragQuestionId: null,
        pickerSelection: new Set(),
        pickerPage: 1,
        pickerTotal: 0,
        pickerKeyword: '',
        pickerLoading: false
    };

    const LEVEL_LABELS = { easy: '基础题', medium: '中档题', hard: '难题' };
    const TYPE_LABELS = {
        knowledge: '知识专题', method: '方法专题', question_type: '题型专题',
        error_prone: '易错专题', variant: '变式专题', multi_solution: '一题多解',
        cross_chapter: '跨章节专题', exam_oriented: '应试专题', wrong_retry: '错题再练',
        micro: '微专题'
    };
    const VIEW_MODE_LABELS = {
        linear: '线性排序', grouped: '分组排列', tree: '树状嵌套',
        tag: '标签视图', relation: '关系视图'
    };
    const LINK_LABELS = {
        prereq: '前置基础', progression: '基础进阶', similar: '同类对比',
        parent_variant: '母题变式', derived_from: '派生来源'
    };
    const SCENE_LABELS = {
        prepare: '备课', lecture: '授课', exercise: '练习',
        research: '教研', exam_review: '考前复盘'
    };

    // ---------------- API 薄封装（自带 X-Local-Token 头） ----------------

    async function api(path, options = {}) {
        const headers = Object.assign({ 'X-Local-Token': window.__mathbankToken || '' }, options.headers || {});
        if (options.json !== undefined) {
            headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(options.json);
        }
        const response = await fetch(path, Object.assign({}, options, { headers }));
        let payload = null;
        try { payload = await response.json(); } catch (e) { payload = null; }
        if (!response.ok) {
            const message = (payload && payload.message) || (payload && payload.detail) || `请求失败 ${response.status}`;
            throw new Error(message);
        }
        return payload || {};
    }

    function toast(message, type) {
        if (window.showToast) window.showToast(message, type || 'info');
    }

    function escapeText(value) {
        return safe ? safe.escapeText(String(value == null ? '' : value)) : String(value == null ? '' : value);
    }

    function escapeAttribute(value) {
        return safe ? safe.escapeAttribute(String(value == null ? '' : value)) : String(value == null ? '' : value);
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
            console.error('[Topic] KaTeX rendering error:', e);
        }
    }

    // ---------------- 模态层 ----------------

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
        if (modal) {
            modal.remove();
        }
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
                    <button type="button" class="text-slate-400 hover:text-slate-600 p-1" aria-label="关闭" data-topic-modal-close="${id}">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </header>
                <div class="flex-1 overflow-y-auto custom-scrollbar p-5" data-topic-modal-body>${bodyHtml || ''}</div>
                ${footerHtml ? `<footer class="px-5 py-3 border-t border-slate-200/70 flex justify-end gap-2" data-topic-modal-footer>${footerHtml}</footer>` : ''}
            </div>`;
        document.body.appendChild(modal);
        modal.addEventListener('click', (event) => {
            if (event.target === modal) closeModal(id);
            const closer = event.target.closest && event.target.closest(`[data-topic-modal-close="${id}"]`);
            if (closer) closeModal(id);
        });
        return modal;
    }

    // ---------------- 配置与列表 ----------------

    async function loadConfig() {
        if (TopicStore.config) return TopicStore.config;
        const result = await api('/api/config/topic-types');
        TopicStore.config = result.data || {};
        return TopicStore.config;
    }

    function typeLabel(code) {
        return TYPE_LABELS[code] || code || '';
    }

    async function loadTopicList() {
        TopicStore.loadingList = true;
        renderListPanel();
        try {
            const params = new URLSearchParams();
            if (TopicStore.keyword) params.set('q', TopicStore.keyword);
            if (TopicStore.typeFilter) params.set('type', TopicStore.typeFilter);
            const result = await api(`/api/topics?${params.toString()}`);
            TopicStore.topics = result.data || [];
        } catch (error) {
            console.error('[Topic] 加载专题列表失败:', error);
            toast(error.message || '加载专题列表失败', 'error');
            TopicStore.topics = [];
        }
        TopicStore.loadingList = false;
        renderListPanel();
    }

    function renderListPanel() {
        const container = document.getElementById('topicListContainer');
        const filters = document.getElementById('topicListFilters');
        if (!container) return;
        if (filters && !filters.dataset.bound) {
            filters.dataset.bound = '1';
            filters.innerHTML = `
                <div class="topic-type-chips" role="group" aria-label="按类型筛选">
                    <button type="button" class="topic-chip is-active" data-topic-type="">全部</button>
                </div>`;
            filters.querySelector('.topic-type-chips').addEventListener('click', (event) => {
                const chip = event.target.closest('.topic-chip');
                if (!chip) return;
                TopicStore.typeFilter = chip.dataset.topicType || '';
                filters.querySelectorAll('.topic-chip').forEach(c => c.classList.toggle('is-active', c === chip));
                loadTopicList();
            });
        }
        if (filters && filters.querySelector('.topic-type-chips').children.length === 1) {
            const chipRow = filters.querySelector('.topic-type-chips');
            Object.keys(TYPE_LABELS).forEach(code => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'topic-chip';
                chip.dataset.topicType = code;
                chip.textContent = TYPE_LABELS[code];
                chipRow.appendChild(chip);
            });
        }

        if (TopicStore.loadingList) {
            container.innerHTML = `
                <div class="ui-state ui-state-loading" role="status" aria-live="polite">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-spinner fa-spin"></i></span>
                    <strong class="ui-state-title">正在载入专题</strong>
                </div>`;
            return;
        }
        if (!TopicStore.topics.length) {
            container.innerHTML = `
                <div class="ui-state ui-state-empty" role="status">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-diagram-project"></i></span>
                    <strong class="ui-state-title">${TopicStore.keyword || TopicStore.typeFilter ? '没有匹配的专题' : '还没有专题'}</strong>
                    <span class="ui-state-description">${TopicStore.keyword || TopicStore.typeFilter ? '调整搜索词或类型筛选试试。' : '点击「新建专题」开始组合题目。'}</span>
                </div>`;
            return;
        }

        const rows = TopicStore.topics.map(topic => {
            const indent = topicDepth(topic);
            const active = topic.id === TopicStore.currentTopicId;
            const badges = [
                topic.is_template ? '<span class="topic-list-badge topic-list-badge-template" title="模板">模板</span>' : '',
                topic.child_count ? `<span class="topic-list-badge" title="子专题 ${topic.child_count} 个">子 ${topic.child_count}</span>` : '',
                topic.status && topic.status !== 'active' ? `<span class="topic-list-badge">${escapeText(topic.status)}</span>` : ''
            ].join('');
            return `
                <button type="button" class="topic-list-item ${active ? 'is-active' : ''}" data-topic-id="${topic.id}" style="padding-left:${12 + indent * 16}px">
                    <span class="topic-list-item-main">
                        <span class="topic-list-item-title">${escapeText(topic.title)}</span>
                        <span class="topic-list-item-meta">
                            <span class="topic-list-type">${escapeText(typeLabel(topic.topic_type))}</span>
                            <span class="topic-list-count" title="题目数">${topic.item_count} 题</span>
                        </span>
                    </span>
                    <span class="topic-list-badges">${badges}</span>
                </button>`;
        }).join('');
        container.innerHTML = `<div class="topic-list-items" role="listbox" aria-label="专题列表">${rows}</div>`;
        container.querySelectorAll('.topic-list-item').forEach(button => {
            button.addEventListener('click', () => {
                selectTopic(parseInt(button.dataset.topicId, 10));
            });
        });
    }

    function topicDepth(topic) {
        // 列表已按 updated_at 排序，父子缩进用 parent_topic_id 在列表中的位置近似。
        const index = TopicStore.topics.findIndex(t => t.id === topic.parent_topic_id);
        return index >= 0 ? 1 : 0;
    }

    async function selectTopic(topicId) {
        TopicStore.currentTopicId = topicId;
        TopicStore.selectedQuestionId = null;
        renderListPanel();
        await loadTopicDetail(topicId);
    }

    // ---------------- 详情 ----------------

    async function loadTopicDetail(topicId) {
        TopicStore.loadingDetail = true;
        renderDetailPanel();
        try {
            const result = await api(`/api/topics/${topicId}`);
            TopicStore.detail = result.data;
            TopicStore.viewMode = (TopicStore.detail.topic && TopicStore.detail.topic.view_mode) || 'linear';
        } catch (error) {
            console.error('[Topic] 加载专题详情失败:', error);
            toast(error.message || '加载专题详情失败', 'error');
            TopicStore.detail = null;
        }
        TopicStore.loadingDetail = false;
        renderDetailPanel();
        renderInspector();
    }

    function difficultyStats() {
        const counts = { easy: 0, medium: 0, hard: 0 };
        (TopicStore.detail.items || []).forEach(item => {
            const level = String((item.question && item.question.difficulty) || '').toLowerCase();
            if (level in counts) counts[level] += 1;
        });
        return counts;
    }

    function renderDifficultyBar() {
        const counts = difficultyStats();
        const total = counts.easy + counts.medium + counts.hard;
        if (!total) return '';
        const segment = (level) => {
            const percent = Math.round((counts[level] / total) * 100);
            if (!percent) return '';
            const color = level === 'easy' ? 'var(--topic-diff-easy, #10b981)' : level === 'medium' ? 'var(--topic-diff-medium, #f59e0b)' : 'var(--topic-diff-hard, #ef4444)';
            return `<span class="topic-diff-segment" style="width:${percent}%;background:${color}" title="${LEVEL_LABELS[level]} ${counts[level]} 题（${percent}%）"></span>`;
        };
        return `
            <div class="topic-difficulty-bar" role="img" aria-label="难度配比：基础 ${counts.easy}、中档 ${counts.medium}、难题 ${counts.hard}">
                ${segment('easy')}${segment('medium')}${segment('hard')}
            </div>
            <div class="topic-difficulty-legend">
                <span><i style="background:#10b981"></i>基础 ${counts.easy}</span>
                <span><i style="background:#f59e0b"></i>中档 ${counts.medium}</span>
                <span><i style="background:#ef4444"></i>难题 ${counts.hard}</span>
            </div>`;
    }

    function renderDetailPanel() {
        const empty = document.getElementById('topicDetailEmpty');
        const content = document.getElementById('topicDetailContent');
        if (!empty || !content) return;
        if (!TopicStore.detail) {
            empty.classList.remove('hidden');
            content.classList.add('hidden');
            if (TopicStore.loadingDetail) {
                empty.querySelector('.ui-state-title').textContent = '正在载入专题';
            } else if (TopicStore.currentTopicId) {
                empty.querySelector('.ui-state-title').textContent = '专题加载失败';
            } else {
                empty.querySelector('.ui-state-title').textContent = '还没有选择专题';
            }
            return;
        }
        empty.classList.add('hidden');
        content.classList.remove('hidden');
        const topic = TopicStore.detail.topic;
        const header = document.getElementById('topicDetailHeader');
        header.innerHTML = `
            <div class="topic-detail-title-row">
                <input id="topicTitleInput" class="topic-title-input" value="${escapeAttribute(topic.title)}" aria-label="专题标题" maxlength="200">
                <select id="topicTypeSelect" class="glass-select" aria-label="专题类型">
                    ${Object.keys(TYPE_LABELS).map(code => `<option value="${code}" ${code === topic.topic_type ? 'selected' : ''}>${TYPE_LABELS[code]}</option>`).join('')}
                </select>
                <select id="topicStatusSelect" class="glass-select" aria-label="专题状态">
                    ${['draft', 'active', 'archived'].map(s => `<option value="${s}" ${s === topic.status ? 'selected' : ''}>${s === 'draft' ? '草稿' : s === 'active' ? '启用' : '归档'}</option>`).join('')}
                </select>
            </div>
            <div class="topic-detail-meta-row">
                <select id="topicParentSelect" class="glass-select" aria-label="父专题">
                    <option value="">无父专题</option>
                    ${TopicStore.topics.filter(t => t.id !== topic.id).map(t => `<option value="${t.id}" ${t.id === topic.parent_topic_id ? 'selected' : ''}>${escapeText(t.title)}</option>`).join('')}
                </select>
                <label class="topic-template-toggle"><input type="checkbox" id="topicTemplateToggle" ${topic.is_template ? 'checked' : ''}><span>设为模板</span></label>
            </div>
            <div class="topic-detail-desc-row">
                <label for="topicDescInput" class="sr-only">专题导语</label>
                <textarea id="topicDescInput" class="glass-input" rows="2" placeholder="专题导语（教学意图说明，支持 Markdown）">${escapeText(topic.description || '')}</textarea>
                <button type="button" class="glass-btn" id="topicSaveMetaBtn"><i class="fa-solid fa-floppy-disk" aria-hidden="true"></i><span>保存信息</span></button>
            </div>
            <div class="topic-detail-actions-row">
                <div class="topic-view-modes" role="group" aria-label="视图模式">
                    ${Object.keys(VIEW_MODE_LABELS).map(code => `<button type="button" class="topic-view-btn ${code === TopicStore.viewMode ? 'is-active' : ''}" data-view-mode="${code}">${VIEW_MODE_LABELS[code]}</button>`).join('')}
                </div>
                <div class="topic-detail-tools">
                    <button type="button" class="glass-btn" id="topicAddQuestionsBtn"><i class="fa-solid fa-plus" aria-hidden="true"></i><span>选题</span></button>
                    <button type="button" class="glass-btn" id="topicRecommendBtn"><i class="fa-solid fa-wand-magic-sparkles" aria-hidden="true"></i><span>智能推荐</span></button>
                    <button type="button" class="glass-btn" id="topicSendToPaperBtn"><i class="fa-solid fa-file-lines" aria-hidden="true"></i><span>送入组卷</span></button>
                    <button type="button" class="glass-btn" id="topicVersionsBtn"><i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i><span>版本 ${TopicStore.detail.version_count || 0}</span></button>
                    <button type="button" class="glass-btn" id="topicUsageBtn"><i class="fa-solid fa-chart-simple" aria-hidden="true"></i><span>学情</span></button>
                    <button type="button" class="glass-btn" id="topicLinksBtn"><i class="fa-solid fa-link" aria-hidden="true"></i><span>关联</span></button>
                    <button type="button" class="glass-btn" id="topicDuplicateBtn"><i class="fa-solid fa-clone" aria-hidden="true"></i><span>复制</span></button>
                    <button type="button" class="glass-btn" id="topicExportBtn"><i class="fa-solid fa-file-export" aria-hidden="true"></i><span>导出</span></button>
                    <button type="button" class="glass-btn" id="topicImportBtn"><i class="fa-solid fa-file-import" aria-hidden="true"></i><span>导入</span></button>
                    <button type="button" class="glass-btn glass-btn-danger" id="topicDeleteBtn"><i class="fa-solid fa-trash-can" aria-hidden="true"></i><span>删除</span></button>
                </div>
            </div>`;
        bindDetailHeader();

        const scroll = document.getElementById('topicDetailScroll');
        if (TopicStore.loadingDetail) {
            scroll.innerHTML = `<div class="ui-state ui-state-loading"><span class="ui-state-icon"><i class="fa-solid fa-spinner fa-spin"></i></span><strong class="ui-state-title">正在载入</strong></div>`;
            return;
        }
        const detail = TopicStore.detail;
        if (!detail.items.length) {
            scroll.innerHTML = `
                <div class="ui-state ui-state-empty">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-inbox"></i></span>
                    <strong class="ui-state-title">专题还是空的</strong>
                    <span class="ui-state-description">点击「选题」从题库添加题目，或用「智能推荐」按同模型/同解法/变式自动发现。</span>
                </div>`;
            return;
        }

        const groupsHtml = renderGroups(detail.groups || []);
        const ungrouped = (detail.items || []).filter(item => !item.group_id);
        const ungroupedHtml = ungrouped.length ? renderItemList(ungrouped, null) : '';
        scroll.innerHTML = `
            <div class="topic-detail-summary">
                <span class="topic-detail-count">共 ${detail.items.length} 题</span>
                ${renderDifficultyBar()}
                <button type="button" class="topic-insights-link" id="topicInsightsBtn">查看配比与学情洞察</button>
            </div>
            ${groupsHtml}
            ${ungroupedHtml}`;
        bindDetailItems();
        renderMath(scroll);
    }

    function renderGroups(groups) {
        if (!groups.length) return '';
        const itemsByGroup = {};
        (TopicStore.detail.items || []).forEach(item => {
            if (item.group_id) {
                (itemsByGroup[item.group_id] = itemsByGroup[item.group_id] || []).push(item);
            }
        });
        return groups.map(group => {
            const groupItems = itemsByGroup[group.id] || [];
            return `
                <section class="topic-group" data-group-id="${group.id}">
                    <header class="topic-group-header">
                        <button type="button" class="topic-group-toggle" aria-expanded="true" aria-label="折叠分组"><i class="fa-solid fa-chevron-down"></i></button>
                        <input class="topic-group-title-input" value="${escapeAttribute(group.title || '')}" aria-label="分组标题" data-group-id="${group.id}">
                        <span class="topic-group-count">${groupItems.length} 题</span>
                        <button type="button" class="topic-group-action" data-group-action="up" data-group-id="${group.id}" title="上移分组" aria-label="上移分组"><i class="fa-solid fa-arrow-up"></i></button>
                        <button type="button" class="topic-group-action" data-group-action="down" data-group-id="${group.id}" title="下移分组" aria-label="下移分组"><i class="fa-solid fa-arrow-down"></i></button>
                        <button type="button" class="topic-group-action" data-group-action="delete" data-group-id="${group.id}" title="删除分组（组内题目移至未分组）" aria-label="删除分组"><i class="fa-solid fa-trash-can"></i></button>
                    </header>
                    <div class="topic-group-body">${renderItemList(groupItems, group.id)}</div>
                </section>`;
        }).join('');
    }

    function renderItemList(items, groupId) {
        if (!items.length) {
            return groupId
                ? '<div class="topic-group-empty">拖拽题目到此处，或先选题再加入本组</div>'
                : '';
        }
        return `<div class="topic-item-list ${groupId ? 'is-grouped' : ''}" data-group-id="${groupId || ''}">${items.map(item => renderItemCard(item)).join('')}</div>`;
    }

    function renderItemCard(item) {
        const question = item.question || {};
        const selected = item.question_id === TopicStore.selectedQuestionId;
        const difficulty = String(question.difficulty || '').toLowerCase();
        const diffLabel = LEVEL_LABELS[difficulty] || question.difficulty || '';
        return `
            <article class="topic-item ${selected ? 'is-selected' : ''}" draggable="true" data-question-id="${item.question_id}" data-item-id="${item.id}">
                <div class="topic-item-drag" aria-hidden="true" title="拖拽排序"><i class="fa-solid fa-grip-vertical"></i></div>
                <div class="topic-item-body" data-topic-item-body>
                    <div class="topic-item-head">
                        <span class="topic-item-seq">#${item.seq_num}</span>
                        <span class="topic-item-type">${escapeText(question.question_type || '')}</span>
                        ${difficulty ? `<span class="topic-item-difficulty diff-${difficulty}">${escapeText(diffLabel)}</span>` : ''}
                        <span class="topic-item-bank-seq" title="题库序号">题库 #${escapeText(item.bank_seq_num == null ? '?' : item.bank_seq_num)}</span>
                    </div>
                    <div class="topic-item-content">${escapeText((question.content || '').slice(0, 120))}${(question.content || '').length > 120 ? '…' : ''}</div>
                    ${item.note ? `<div class="topic-item-note" title="题目级批注"><i class="fa-solid fa-note-sticky"></i>${escapeText(item.note)}</div>` : ''}
                </div>
                <div class="topic-item-actions">
                    <button type="button" class="topic-item-action" data-item-action="up" data-item-id="${item.id}" title="上移" aria-label="上移"><i class="fa-solid fa-arrow-up"></i></button>
                    <button type="button" class="topic-item-action" data-item-action="down" data-item-id="${item.id}" title="下移" aria-label="下移"><i class="fa-solid fa-arrow-down"></i></button>
                    <button type="button" class="topic-item-action" data-item-action="note" data-item-id="${item.id}" title="编辑批注" aria-label="编辑批注"><i class="fa-solid fa-pen-to-square"></i></button>
                    <button type="button" class="topic-item-action" data-item-action="open-bank" data-question-id="${item.question_id}" title="在题库中打开" aria-label="在题库中打开"><i class="fa-solid fa-up-right-from-square"></i></button>
                    <button type="button" class="topic-item-action topic-item-action-danger" data-item-action="remove" data-item-id="${item.id}" title="移出专题" aria-label="移出专题"><i class="fa-solid fa-xmark"></i></button>
                </div>
            </article>`;
    }

    function bindDetailHeader() {
        const header = document.getElementById('topicDetailHeader');
        if (!header) return;
        header.querySelectorAll('.topic-view-btn').forEach(button => {
            button.addEventListener('click', async () => {
                const mode = button.dataset.viewMode;
                if (mode === TopicStore.viewMode) return;
                TopicStore.viewMode = mode;
                try {
                    await api(`/api/topics/${TopicStore.currentTopicId}`, { method: 'PUT', json: { view_mode: mode } });
                } catch (error) {
                    toast(error.message || '切换视图失败', 'error');
                }
                renderDetailPanel();
            });
        });
        const saveMeta = header.querySelector('#topicSaveMetaBtn');
        if (saveMeta) {
            saveMeta.addEventListener('click', async () => {
                const payload = {
                    title: header.querySelector('#topicTitleInput').value.trim(),
                    topic_type: header.querySelector('#topicTypeSelect').value,
                    status: header.querySelector('#topicStatusSelect').value,
                    description: header.querySelector('#topicDescInput').value,
                    is_template: header.querySelector('#topicTemplateToggle').checked,
                    parent_topic_id: header.querySelector('#topicParentSelect').value || null
                };
                try {
                    await api(`/api/topics/${TopicStore.currentTopicId}`, { method: 'PUT', json: payload });
                    toast('专题信息已保存', 'success');
                    await Promise.all([loadTopicList(), loadTopicDetail(TopicStore.currentTopicId)]);
                } catch (error) {
                    toast(error.message || '保存失败', 'error');
                }
            });
        }
        const addBtn = header.querySelector('#topicAddQuestionsBtn');
        if (addBtn) addBtn.addEventListener('click', () => openQuestionPicker());
        const recommendBtn = header.querySelector('#topicRecommendBtn');
        if (recommendBtn) recommendBtn.addEventListener('click', () => openRecommendationsPanel());
        const sendBtn = header.querySelector('#topicSendToPaperBtn');
        if (sendBtn) sendBtn.addEventListener('click', sendToPaper);
        const versionsBtn = header.querySelector('#topicVersionsBtn');
        if (versionsBtn) versionsBtn.addEventListener('click', () => openVersionDrawer());
        const usageBtn = header.querySelector('#topicUsageBtn');
        if (usageBtn) usageBtn.addEventListener('click', () => openUsageForm());
        const linksBtn = header.querySelector('#topicLinksBtn');
        if (linksBtn) linksBtn.addEventListener('click', () => openLinkEditor());
        const duplicateBtn = header.querySelector('#topicDuplicateBtn');
        if (duplicateBtn) duplicateBtn.addEventListener('click', duplicateCurrentTopic);
        const exportBtn = header.querySelector('#topicExportBtn');
        if (exportBtn) exportBtn.addEventListener('click', exportCurrentTopic);
        const importBtn = header.querySelector('#topicImportBtn');
        if (importBtn) importBtn.addEventListener('click', openImportModal);
        const deleteBtn = header.querySelector('#topicDeleteBtn');
        if (deleteBtn) deleteBtn.addEventListener('click', deleteCurrentTopic);
    }

    function bindDetailItems() {
        const scroll = document.getElementById('topicDetailScroll');
        if (!scroll) return;
        scroll.querySelectorAll('[data-topic-item-body]').forEach(body => {
            body.addEventListener('click', () => {
                const card = body.closest('.topic-item');
                const questionId = parseInt(card.dataset.questionId, 10);
                TopicStore.selectedQuestionId = TopicStore.selectedQuestionId === questionId ? null : questionId;
                renderDetailPanel();
                renderInspector();
            });
        });
        scroll.querySelectorAll('[data-item-action]').forEach(button => {
            button.addEventListener('click', async (event) => {
                event.stopPropagation();
                const action = button.dataset.itemAction;
                const itemId = parseInt(button.dataset.itemId, 10);
                if (action === 'up' || action === 'down') {
                    moveItem(itemId, action === 'up' ? -1 : 1);
                } else if (action === 'note') {
                    editItemNote(itemId);
                } else if (action === 'remove') {
                    removeItem(itemId);
                } else if (action === 'open-bank') {
                    openInBank(parseInt(button.dataset.questionId, 10));
                }
            });
        });
        scroll.querySelectorAll('.topic-group-action').forEach(button => {
            button.addEventListener('click', async () => {
                const action = button.dataset.groupAction;
                const groupId = parseInt(button.dataset.groupId, 10);
                if (action === 'delete') {
                    deleteGroup(groupId);
                } else {
                    moveGroup(groupId, action === 'up' ? -1 : 1);
                }
            });
        });
        scroll.querySelectorAll('.topic-group-title-input').forEach(input => {
            input.addEventListener('change', async () => {
                try {
                    await api(`/api/topics/${TopicStore.currentTopicId}/groups/${input.dataset.groupId}`, {
                        method: 'PUT', json: { title: input.value.trim() }
                    });
                } catch (error) {
                    toast(error.message || '分组改名失败', 'error');
                }
            });
        });
        scroll.querySelectorAll('.topic-group-toggle').forEach(button => {
            button.addEventListener('click', () => {
                const group = button.closest('.topic-group');
                const body = group.querySelector('.topic-group-body');
                const expanded = button.getAttribute('aria-expanded') === 'true';
                button.setAttribute('aria-expanded', String(!expanded));
                body.classList.toggle('is-collapsed', expanded);
                button.querySelector('i').className = expanded ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down';
            });
        });
        bindDragAndDrop(scroll);
    }

    // ---------------- 排序 / 增删 ----------------

    async function persistOrder() {
        const ordered = (TopicStore.detail.items || []).map(item => item.question_id);
        try {
            await api(`/api/topics/${TopicStore.currentTopicId}/items/order`, {
                method: 'PUT', json: { question_ids: ordered }
            });
        } catch (error) {
            toast(error.message || '排序保存失败', 'error');
            await loadTopicDetail(TopicStore.currentTopicId);
        }
    }

    async function moveItem(itemId, delta) {
        const items = TopicStore.detail.items || [];
        const index = items.findIndex(item => item.id === itemId);
        if (index < 0) return;
        const target = index + delta;
        if (target < 0 || target >= items.length) return;
        const moved = items.splice(index, 1)[0];
        items.splice(target, 0, moved);
        items.forEach((item, position) => { item.position = position; item.seq_num = position + 1; });
        renderDetailPanel();
        await persistOrder();
    }

    async function removeItem(itemId) {
        const item = (TopicStore.detail.items || []).find(entry => entry.id === itemId);
        if (!item) return;
        if (!confirm(`确定把「${(item.question && item.question.content || '').slice(0, 20)}…」移出专题吗？（题目本身不会删除）`)) return;
        try {
            await api(`/api/topics/${TopicStore.currentTopicId}/items/${itemId}`, { method: 'DELETE' });
            toast('已移出专题', 'success');
            await loadTopicDetail(TopicStore.currentTopicId);
            await loadTopicList();
        } catch (error) {
            toast(error.message || '移出失败', 'error');
        }
    }

    async function editItemNote(itemId) {
        const item = (TopicStore.detail.items || []).find(entry => entry.id === itemId);
        if (!item) return;
        const value = prompt('题目级批注（本题在本专题中的讲解角度）：', item.note || '');
        if (value === null) return;
        try {
            await api(`/api/topics/${TopicStore.currentTopicId}/items/${itemId}`, {
                method: 'PUT', json: { note: value }
            });
            await loadTopicDetail(TopicStore.currentTopicId);
        } catch (error) {
            toast(error.message || '批注保存失败', 'error');
        }
    }

    async function persistGroupOrder() {
        const ordered = (TopicStore.detail.groups || []).map(group => group.id);
        try {
            await api(`/api/topics/${TopicStore.currentTopicId}/groups/order`, {
                method: 'PUT', json: { group_ids: ordered }
            });
        } catch (error) {
            toast(error.message || '分组顺序保存失败', 'error');
            await loadTopicDetail(TopicStore.currentTopicId);
        }
    }

    async function moveGroup(groupId, delta) {
        const groups = (TopicStore.detail.groups || []).slice();
        const index = groups.findIndex(group => group.id === groupId);
        if (index < 0) return;
        const target = index + delta;
        if (target < 0 || target >= groups.length) return;
        const moved = groups.splice(index, 1)[0];
        groups.splice(target, 0, moved);
        groups.forEach((group, position) => { group.position = position; });
        TopicStore.detail.groups = groups;
        renderDetailPanel();
        await persistGroupOrder();
    }

    async function deleteGroup(groupId) {
        if (!confirm('删除分组？组内题目将移至未分组（不会删除题目）。')) return;
        try {
            await api(`/api/topics/${TopicStore.currentTopicId}/groups/${groupId}`, { method: 'DELETE' });
            toast('分组已删除', 'success');
            await loadTopicDetail(TopicStore.currentTopicId);
        } catch (error) {
            toast(error.message || '删除分组失败', 'error');
        }
    }

    async function addQuestions(questionIds, groupId) {
        try {
            const result = await api(`/api/topics/${TopicStore.currentTopicId}/items`, {
                method: 'POST',
                json: { question_ids: questionIds, group_id: groupId || null }
            });
            const added = (result.data && result.data.added) || [];
            const skipped = (result.data && result.data.skipped) || [];
            if (added.length) toast(`已加入 ${added.length} 题${skipped.length ? `（${skipped.length} 题已在专题中，已跳过）` : ''}`, 'success');
            else if (skipped.length) toast('所选题目已在专题中', 'info');
            await Promise.all([loadTopicDetail(TopicStore.currentTopicId), loadTopicList()]);
            return result.data;
        } catch (error) {
            toast(error.message || '添加题目失败', 'error');
            return null;
        }
    }

    // ---------------- 拖拽（照 paper.js 模式：乐观重排 + 落库） ----------------

    function bindDragAndDrop(scroll) {
        const cards = scroll.querySelectorAll('.topic-item');
        cards.forEach(card => {
            card.addEventListener('dragstart', (event) => {
                TopicStore.dragQuestionId = parseInt(card.dataset.questionId, 10);
                card.classList.add('is-dragging');
                if (event.dataTransfer) {
                    event.dataTransfer.effectAllowed = 'move';
                    try { event.dataTransfer.setData('text/plain', String(TopicStore.dragQuestionId)); } catch (e) { /* ignore */ }
                }
                debug.debug('topic dragstart', TopicStore.dragQuestionId);
            });
            card.addEventListener('dragend', () => {
                card.classList.remove('is-dragging');
                scroll.querySelectorAll('.topic-item').forEach(node => node.classList.remove('is-drop-target'));
                TopicStore.dragQuestionId = null;
            });
            card.addEventListener('dragover', (event) => {
                if (TopicStore.dragQuestionId === null) return;
                event.preventDefault();
                scroll.querySelectorAll('.topic-item').forEach(node => node.classList.remove('is-drop-target'));
                card.classList.add('is-drop-target');
            });
            card.addEventListener('drop', async (event) => {
                if (TopicStore.dragQuestionId === null) return;
                event.preventDefault();
                const targetId = parseInt(card.dataset.questionId, 10);
                const sourceId = TopicStore.dragQuestionId;
                if (sourceId === targetId) return;
                const items = (TopicStore.detail.items || []).slice();
                const from = items.findIndex(item => item.question_id === sourceId);
                const to = items.findIndex(item => item.question_id === targetId);
                if (from < 0 || to < 0) return;
                const moved = items.splice(from, 1)[0];
                items.splice(to, 0, moved);
                items.forEach((item, position) => { item.position = position; item.seq_num = position + 1; });
                renderDetailPanel();
                await persistOrder();
                debug.debug('topic drop reordered', sourceId, '->', targetId);
            });
        });
    }

    // ---------------- 检查器 ----------------

    function renderInspector() {
        const questionPanel = document.getElementById('topicInspectorQuestion');
        const infoPanel = document.getElementById('topicInspectorInfo');
        if (!questionPanel || !infoPanel) return;
        const detail = TopicStore.detail;
        if (!detail) {
            questionPanel.innerHTML = '';
            infoPanel.innerHTML = '';
            return;
        }
        const topic = detail.topic;
        const selected = (detail.items || []).find(item => item.question_id === TopicStore.selectedQuestionId);
        if (selected) {
            const question = selected.question || {};
            questionPanel.innerHTML = `
                <header class="topic-inspector-head">
                    <strong><i class="fa-solid fa-file-lines" aria-hidden="true"></i> 题目预览</strong>
                    <span class="topic-inspector-seq">专题 #${selected.seq_num} · 题库 #${escapeText(selected.bank_seq_num == null ? '?' : selected.bank_seq_num)}</span>
                </header>
                <div class="topic-inspector-question topic-marked" id="topicInspectorQuestionBody"></div>
                ${selected.note ? `<div class="topic-inspector-note"><strong>批注：</strong>${escapeText(selected.note)}</div>` : ''}
                <div class="topic-inspector-actions">
                    <button type="button" class="glass-btn" id="topicInspectorOpenBank" data-question-id="${question.id}"><i class="fa-solid fa-up-right-from-square" aria-hidden="true"></i><span>在题库中打开</span></button>
                </div>`;
            const body = questionPanel.querySelector('#topicInspectorQuestionBody');
            body.innerHTML = window.marked ? window.marked.parse(String(question.content || '')) : escapeText(question.content || '');
            renderMath(body);
            const openBank = questionPanel.querySelector('#topicInspectorOpenBank');
            if (openBank) openBank.addEventListener('click', () => openInBank(question.id));
        } else {
            questionPanel.innerHTML = `
                <header class="topic-inspector-head"><strong><i class="fa-solid fa-file-lines" aria-hidden="true"></i> 题目预览</strong></header>
                <div class="ui-state ui-state-empty"><span class="ui-state-description">点击专题内的题目卡查看题干与解析预览。</span></div>`;
        }
        const children = detail.children || [];
        const links = detail.links || [];
        infoPanel.innerHTML = `
            <header class="topic-inspector-head"><strong><i class="fa-solid fa-circle-info" aria-hidden="true"></i> 专题信息</strong></header>
            <dl class="topic-info-list">
                <div><dt>类型</dt><dd>${escapeText(typeLabel(topic.topic_type))}</dd></div>
                <div><dt>状态</dt><dd>${escapeText(topic.status)}</dd></div>
                <div><dt>题目数</dt><dd>${(detail.items || []).length}</dd></div>
                <div><dt>使用次数</dt><dd>${topic.usage_count || 0}</dd></div>
                <div><dt>最近使用</dt><dd>${topic.last_used_at ? escapeText(String(topic.last_used_at).slice(0, 10)) : '尚未使用'}</dd></div>
                <div><dt>版本数</dt><dd>${detail.version_count || 0}</dd></div>
            </dl>
            ${children.length ? `<div class="topic-info-block"><strong>子专题（${children.length}）</strong><ul>${children.map(child => `<li>${escapeText(child.title)}</li>`).join('')}</ul></div>` : ''}
            ${links.length ? `<div class="topic-info-block"><strong>关联专题（${links.length}）</strong><ul>${links.map(link => `<li>${escapeText(LINK_LABELS[link.link_code] || link.link_code)} → ${escapeText((link.other_topic && link.other_topic.title) || '?')}</li>`).join('')}</ul></div>` : ''}
            ${topic.description ? `<div class="topic-info-block"><strong>导语</strong><p class="topic-info-desc">${escapeText(topic.description)}</p></div>` : ''}`;
    }

    // ---------------- 互跳桥 ----------------

    function sendToPaper() {
        const items = (TopicStore.detail && TopicStore.detail.items) || [];
        if (!items.length) {
            toast('专题内还没有题目', 'info');
            return;
        }
        if (typeof window.addToCart !== 'function') {
            toast('组卷模块尚未加载', 'error');
            return;
        }
        let added = 0;
        items.forEach(item => {
            const question = item.question || {};
            window.addToCart(item.question_id, question.question_type === 'detailed_answer' ? 12 : 5);
            added += 1;
        });
        toast(`已按顺序送入组卷 ${added} 题`, 'success');
        if (typeof window.selectWorkspace === 'function') window.selectWorkspace('paper', '智能组卷');
    }

    function openInBank(questionId) {
        if (typeof window.selectWorkspace !== 'function') return;
        window.selectWorkspace('bank', '题库管理');
        // 钩子式：选中后由题库自己的工作区完成渲染；这里只做工作区切换与搜索预填。
        try {
            const search = document.getElementById('globalSearchInput') || document.getElementById('searchInput');
            if (search) {
                search.value = `#${questionId}`;
                if (typeof window.loadQuestions === 'function') window.loadQuestions();
            }
        } catch (e) {
            debug.debug('topic openInBank fallback', e);
        }
    }

    // ---------------- 创建方式 ----------------

    function toggleTopicCreateMenu() {
        const list = document.getElementById('topicCreateMenuList');
        const button = document.getElementById('topicCreateMenuBtn');
        if (!list || !button) return;
        const willShow = list.classList.contains('hidden');
        list.classList.toggle('hidden', !willShow);
        button.setAttribute('aria-expanded', String(willShow));
    }

    async function createTopicBlank() {
        const title = prompt('新专题标题：', '');
        if (title === null) return;
        if (!title.trim()) {
            toast('专题标题不能为空', 'error');
            return;
        }
        try {
            const result = await api('/api/topics', { method: 'POST', json: { title: title.trim() } });
            toast('专题已创建', 'success');
            await loadTopicList();
            await selectTopic(result.data.id);
        } catch (error) {
            toast(error.message || '创建失败', 'error');
        }
    }

    async function openTopicFromTagsModal() {
        const modal = ensureModalShell('topicFromTagsModal', '从标签生成专题', `
            <div class="topic-form-grid">
                <label>专题标题<input id="fromTagsTitle" class="glass-input" placeholder="留空则按标签自动命名"></label>
                <label>标签维度
                    <select id="fromTagsDim" class="glass-select">
                        <option value="strategy">中观策略</option>
                        <option value="micro">微观技法</option>
                        <option value="thought">宏观思想</option>
                        <option value="function">功能</option>
                        <option value="chapter">章节</option>
                        <option value="custom">自定义标签</option>
                    </select>
                </label>
                <label>标签代码（逗号分隔）<input id="fromTagsCodes" class="glass-input" placeholder="例如：huanfa, guide_number"></label>
                <label>题目数量上限<input id="fromTagsLimit" class="glass-input" type="number" value="12" min="5" max="30"></label>
                <label>专题类型
                    <select id="fromTagsType" class="glass-select">
                        ${Object.keys(TYPE_LABELS).map(code => `<option value="${code}" ${code === 'method' ? 'selected' : ''}>${TYPE_LABELS[code]}</option>`).join('')}
                    </select>
                </label>
            </div>
            <p class="topic-form-hint">按难度分层抽样；层内优先选有答案、被多次验证过的题目。</p>`, `
            <button type="button" class="glass-btn" data-topic-modal-close="topicFromTagsModal">取消</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="fromTagsSubmit">生成专题</button>`);
        openModal('topicFromTagsModal');
        modal.querySelector('#fromTagsSubmit').addEventListener('click', async () => {
            const codes = modal.querySelector('#fromTagsCodes').value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
            if (!codes.length) {
                toast('请填写至少一个标签代码', 'error');
                return;
            }
            try {
                const result = await api('/api/topics/from-tags', {
                    method: 'POST',
                    json: {
                        title: modal.querySelector('#fromTagsTitle').value.trim(),
                        tag_dim: modal.querySelector('#fromTagsDim').value,
                        tag_codes: codes,
                        max_questions: parseInt(modal.querySelector('#fromTagsLimit').value, 10) || 12,
                        topic_type: modal.querySelector('#fromTagsType').value
                    }
                });
                closeModal('topicFromTagsModal');
                toast(`已生成专题（候选 ${result.data.candidate_count} 题，入选 ${result.data.question_ids.length} 题）`, 'success');
                await loadTopicList();
                await selectTopic(result.data.id);
            } catch (error) {
                toast(error.message || '生成失败', 'error');
            }
        });
    }

    async function openTopicFromPaperModal() {
        let papers = [];
        try {
            const result = await api('/api/papers');
            papers = (result.data || result) || [];
        } catch (error) {
            toast('加载试卷列表失败', 'error');
            return;
        }
        const modal = ensureModalShell('topicFromPaperModal', '从试卷反推专题', `
            <label>选择试卷
                <select id="fromPaperSelect" class="glass-select">
                    ${papers.map(paper => `<option value="${paper.id}">${escapeText(paper.title || '未命名试卷')}</option>`).join('')}
                </select>
            </label>
            <p class="topic-form-hint">按章节聚类；≥6 题且跨题型才按题型二次拆分；&lt;3 题的簇只报告不静默建专题。</p>
            <div id="fromPaperPreview" class="topic-preview-list"></div>`, `
            <button type="button" class="glass-btn" data-topic-modal-close="topicFromPaperModal">取消</button>
            <button type="button" class="glass-btn" id="fromPaperPreviewBtn">预览草稿</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="fromPaperSubmit">直接创建</button>`);
        openModal('topicFromPaperModal');
        modal.querySelector('#fromPaperPreviewBtn').addEventListener('click', async () => {
            const paperId = parseInt(modal.querySelector('#fromPaperSelect').value, 10);
            try {
                const result = await api(`/api/topics/from-paper/${paperId}`, { method: 'POST', json: { dry_run: true } });
                const preview = modal.querySelector('#fromPaperPreview');
                preview.innerHTML = (result.data.drafts || []).map(draft => `
                    <div class="topic-preview-item"><strong>${escapeText(draft.title)}</strong><span>${draft.question_ids.length} 题</span></div>`).join('')
                    + ((result.data.orphans || []).length ? `<div class="topic-preview-orphans">未建专题题目：${result.data.orphans.length} 题（${escapeText((result.data.orphans[0] || {}).reason || '')}）</div>` : '');
            } catch (error) {
                toast(error.message || '预览失败', 'error');
            }
        });
        modal.querySelector('#fromPaperSubmit').addEventListener('click', async () => {
            const paperId = parseInt(modal.querySelector('#fromPaperSelect').value, 10);
            try {
                const result = await api(`/api/topics/from-paper/${paperId}`, { method: 'POST', json: { dry_run: false } });
                closeModal('topicFromPaperModal');
                toast(`已创建 ${(result.data.created || []).length} 个专题`, 'success');
                await loadTopicList();
                const first = (result.data.created || [])[0];
                if (first) await selectTopic(first.id);
            } catch (error) {
                toast(error.message || '创建失败', 'error');
            }
        });
    }

    async function openTopicFromCurriculumModal() {
        const modal = ensureModalShell('topicFromCurriculumModal', '从课程树节点生成专题', `
            <div class="topic-form-grid">
                <label>必修<input id="fromCurCompulsory" class="glass-input" placeholder="例如：必修第一册（可空）"></label>
                <label>章节<input id="fromCurChapter" class="glass-input" placeholder="例如：第2章（可空）"></label>
                <label>知识点<input id="fromCurKnowledge" class="glass-input" placeholder="例如：函数的单调性（可空）"></label>
                <label>题目数量<input id="fromCurLimit" class="glass-input" type="number" value="12" min="1" max="300"></label>
            </div>
            <p class="topic-form-hint">知识图谱生成的务实降级：直接复用课程索引按节点选题；至少填写一项。</p>`, `
            <button type="button" class="glass-btn" data-topic-modal-close="topicFromCurriculumModal">取消</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="fromCurSubmit">生成专题</button>`);
        openModal('topicFromCurriculumModal');
        modal.querySelector('#fromCurSubmit').addEventListener('click', async () => {
            try {
                const result = await api('/api/topics/from-curriculum', {
                    method: 'POST',
                    json: {
                        compulsory: modal.querySelector('#fromCurCompulsory').value.trim() || null,
                        chapter: modal.querySelector('#fromCurChapter').value.trim() || null,
                        knowledge: modal.querySelector('#fromCurKnowledge').value.trim() || null,
                        limit: parseInt(modal.querySelector('#fromCurLimit').value, 10) || 12
                    }
                });
                closeModal('topicFromCurriculumModal');
                toast(`已生成专题（${result.data.question_ids.length} 题）`, 'success');
                await loadTopicList();
                await selectTopic(result.data.id);
            } catch (error) {
                toast(error.message || '生成失败', 'error');
            }
        });
    }

    // ---------------- 选题器 ----------------

    async function openQuestionPicker() {
        TopicStore.pickerSelection = new Set();
        TopicStore.pickerPage = 1;
        TopicStore.pickerKeyword = '';
        const modal = ensureModalShell('topicQuestionPicker', '选题加入专题', `
            <div class="topic-picker-toolbar">
                <label for="topicPickerSearch" class="sr-only">搜索题目</label>
                <input id="topicPickerSearch" class="glass-input" placeholder="搜索题干 / 来源 / 标签">
                <button type="button" class="glass-btn" id="topicPickerSearchBtn"><i class="fa-solid fa-magnifying-glass"></i><span>搜索</span></button>
                <span class="topic-picker-count" id="topicPickerCount">已选 0 题</span>
            </div>
            <div class="topic-picker-list custom-scrollbar" id="topicPickerList"></div>`, `
            <button type="button" class="glass-btn" data-topic-modal-close="topicQuestionPicker">取消</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="topicPickerSubmit">加入专题</button>`);
        openModal('topicQuestionPicker');
        const searchInput = modal.querySelector('#topicPickerSearch');
        const runSearch = () => {
            TopicStore.pickerKeyword = searchInput.value.trim();
            TopicStore.pickerPage = 1;
            loadPickerPage(modal);
        };
        modal.querySelector('#topicPickerSearchBtn').addEventListener('click', runSearch);
        searchInput.addEventListener('keydown', (event) => { if (event.key === 'Enter') runSearch(); });
        modal.querySelector('#topicPickerSubmit').addEventListener('click', async () => {
            if (!TopicStore.pickerSelection.size) {
                toast('请先选择题目', 'info');
                return;
            }
            const result = await addQuestions(Array.from(TopicStore.pickerSelection));
            if (result) closeModal('topicQuestionPicker');
        });
        await loadPickerPage(modal);
    }

    async function loadPickerPage(modal) {
        const list = modal.querySelector('#topicPickerList');
        const count = modal.querySelector('#topicPickerCount');
        TopicStore.pickerLoading = true;
        list.innerHTML = `<div class="ui-state ui-state-loading"><span class="ui-state-icon"><i class="fa-solid fa-spinner fa-spin"></i></span></div>`;
        try {
            const params = new URLSearchParams({ page: String(TopicStore.pickerPage), page_size: '20' });
            if (TopicStore.pickerKeyword) params.set('q', TopicStore.pickerKeyword);
            const result = await api(`/api/questions?${params.toString()}`);
            const questions = result.data || result.questions || [];
            TopicStore.pickerTotal = result.total || questions.length;
            if (!questions.length) {
                list.innerHTML = `<div class="ui-state ui-state-empty"><span class="ui-state-description">没有找到题目</span></div>`;
                return;
            }
            list.innerHTML = questions.map(question => {
                const checked = TopicStore.pickerSelection.has(question.id) ? 'checked' : '';
                return `
                    <label class="topic-picker-item ${checked ? 'is-selected' : ''}">
                        <input type="checkbox" value="${question.id}" ${checked}>
                        <span class="topic-picker-item-seq">#${escapeText(question.seq_num)}</span>
                        <span class="topic-picker-item-content">${escapeText((question.content || '').slice(0, 80))}</span>
                        <span class="topic-picker-item-type">${escapeText(question.question_type || '')}</span>
                    </label>`;
            }).join('') + `
                <div class="topic-picker-pagination">
                    <button type="button" class="glass-btn" id="pickerPrevPage" ${TopicStore.pickerPage <= 1 ? 'disabled' : ''}>上一页</button>
                    <span>第 ${TopicStore.pickerPage} 页</span>
                    <button type="button" class="glass-btn" id="pickerNextPage" ${questions.length < 20 ? 'disabled' : ''}>下一页</button>
                </div>`;
            list.querySelectorAll('input[type="checkbox"]').forEach(box => {
                box.addEventListener('change', () => {
                    const id = parseInt(box.value, 10);
                    if (box.checked) TopicStore.pickerSelection.add(id);
                    else TopicStore.pickerSelection.delete(id);
                    box.closest('.topic-picker-item').classList.toggle('is-selected', box.checked);
                    count.textContent = `已选 ${TopicStore.pickerSelection.size} 题`;
                });
            });
            const prev = list.querySelector('#pickerPrevPage');
            const next = list.querySelector('#pickerNextPage');
            if (prev) prev.addEventListener('click', () => { TopicStore.pickerPage -= 1; loadPickerPage(modal); });
            if (next) next.addEventListener('click', () => { TopicStore.pickerPage += 1; loadPickerPage(modal); });
        } catch (error) {
            list.innerHTML = `<div class="ui-state ui-state-error"><span class="ui-state-description">${escapeText(error.message || '加载失败')}</span></div>`;
        } finally {
            TopicStore.pickerLoading = false;
        }
    }

    // ---------------- 推荐面板 ----------------

    async function openRecommendationsPanel() {
        const modal = ensureModalShell('topicRecommendations', '智能推荐（规则引擎）', `
            <p class="topic-form-hint">同模型 / 同解法 / 变式推荐，全部基于现有标签与关系数据，可解释、离线。</p>
            <div class="topic-picker-toolbar">
                <label for="topicRecLimit">推荐数量</label>
                <input id="topicRecLimit" class="glass-input" type="number" value="5" min="1" max="20">
                <label class="topic-template-toggle"><input type="checkbox" id="topicRecDiversity" checked><span>去同质化</span></label>
                <button type="button" class="glass-btn" id="topicRecRefreshBtn"><i class="fa-solid fa-rotate"></i><span>重新计算</span></button>
                <button type="button" class="glass-btn" id="topicRecAiBtn"><i class="fa-solid fa-robot"></i><span>AI 润色（可选）</span></button>
            </div>
            <div class="topic-recommend-list custom-scrollbar" id="topicRecommendList"></div>`, `
            <button type="button" class="glass-btn" data-topic-modal-close="topicRecommendations">关闭</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="topicRecSubmit">加入所选</button>`);
        openModal('topicRecommendations');
        const loadRecommendations = async () => {
            const list = modal.querySelector('#topicRecommendList');
            list.innerHTML = `<div class="ui-state ui-state-loading"><span class="ui-state-icon"><i class="fa-solid fa-spinner fa-spin"></i></span></div>`;
            try {
                const limit = parseInt(modal.querySelector('#topicRecLimit').value, 10) || 5;
                const diversity = modal.querySelector('#topicRecDiversity').checked;
                const result = await api(`/api/topics/${TopicStore.currentTopicId}/recommendations?limit=${limit}&diversity=${diversity}`);
                const items = result.data || [];
                if (!items.length) {
                    list.innerHTML = `<div class="ui-state ui-state-empty"><span class="ui-state-description">没有达到推荐阈值的候选题目</span></div>`;
                    return;
                }
                list.innerHTML = items.map(item => `
                    <label class="topic-recommend-item">
                        <input type="checkbox" value="${item.question_id}">
                        <span class="topic-recommend-score" title="匹配度">${(item.score * 100).toFixed(0)}</span>
                        <span class="topic-recommend-body">
                            <span class="topic-recommend-id">题目 #${item.question_id}</span>
                            <span class="topic-recommend-reasons">${item.reasons.map(r => escapeText(r)).join('；')}</span>
                        </span>
                    </label>`).join('');
                list.querySelectorAll('input[type="checkbox"]').forEach(box => {
                    box.addEventListener('change', () => box.closest('.topic-recommend-item').classList.toggle('is-selected', box.checked));
                });
            } catch (error) {
                list.innerHTML = `<div class="ui-state ui-state-error"><span class="ui-state-description">${escapeText(error.message || '推荐失败')}</span></div>`;
            }
        };
        modal.querySelector('#topicRecRefreshBtn').addEventListener('click', loadRecommendations);
        modal.querySelector('#topicRecAiBtn').addEventListener('click', async () => {
            try {
                const result = await api(`/api/topics/${TopicStore.currentTopicId}/ai-recommend`, {
                    method: 'POST',
                    json: { limit: parseInt(modal.querySelector('#topicRecLimit').value, 10) || 5 }
                });
                const selected = (result.data && result.data.selected) || [];
                if (!selected.length) {
                    toast(result.data && result.data.message ? result.data.message : 'AI 未选出题目', 'info');
                    return;
                }
                const ids = selected.map(entry => entry.question_id);
                const added = await addQuestions(ids);
                if (added) {
                    closeModal('topicRecommendations');
                    toast('AI 建议题目已加入（请人工复核）', 'success');
                }
            } catch (error) {
                toast(error.message || 'AI 推荐失败', 'error');
            }
        });
        modal.querySelector('#topicRecSubmit').addEventListener('click', async () => {
            const ids = Array.from(modal.querySelectorAll('#topicRecommendList input[type="checkbox"]:checked')).map(box => parseInt(box.value, 10));
            if (!ids.length) {
                toast('请先选择推荐题目', 'info');
                return;
            }
            const result = await addQuestions(ids);
            if (result) closeModal('topicRecommendations');
        });
        await loadRecommendations();
    }

    // ---------------- 版本 / 学情 / 关联 ----------------

    async function openVersionDrawer() {
        const modal = ensureModalShell('topicVersionsModal', '版本历史', `
            <p class="topic-form-hint">回滚 = 追加新版本：先为当前状态存档，再整体替换，历史只增不减。</p>
            <div class="topic-version-list custom-scrollbar" id="topicVersionList"></div>`, `
            <button type="button" class="glass-btn" data-topic-modal-close="topicVersionsModal">关闭</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="topicVersionSaveBtn">保存当前为新版本</button>`);
        openModal('topicVersionsModal');
        const loadVersions = async () => {
            const list = modal.querySelector('#topicVersionList');
            list.innerHTML = `<div class="ui-state ui-state-loading"><span class="ui-state-icon"><i class="fa-solid fa-spinner fa-spin"></i></span></div>`;
            try {
                const result = await api(`/api/topics/${TopicStore.currentTopicId}/versions`);
                const versions = result.data || [];
                if (!versions.length) {
                    list.innerHTML = `<div class="ui-state ui-state-empty"><span class="ui-state-description">还没有版本，点击右下角保存第一版</span></div>`;
                    return;
                }
                list.innerHTML = versions.map(version => `
                    <div class="topic-version-item">
                        <span class="topic-version-no">v${version.version_no}</span>
                        <span class="topic-version-meta">${escapeText(String(version.created_at || '').slice(0, 19))} · ${version.item_count} 题</span>
                        <span class="topic-version-note">${escapeText(version.note || '')}</span>
                        <span class="topic-version-actions">
                            <button type="button" class="glass-btn" data-version-restore="${version.version_no}">恢复</button>
                            <button type="button" class="glass-btn glass-btn-danger" data-version-delete="${version.version_no}">删除</button>
                        </span>
                    </div>`).join('');
                list.querySelectorAll('[data-version-restore]').forEach(button => {
                    button.addEventListener('click', async () => {
                        if (!confirm(`确定恢复到版本 v${button.dataset.versionRestore} 吗？当前状态会先自动存档。`)) return;
                        try {
                            await api(`/api/topics/${TopicStore.currentTopicId}/versions/${button.dataset.versionRestore}/restore`, { method: 'POST', json: {} });
                            toast('已恢复', 'success');
                            closeModal('topicVersionsModal');
                            await loadTopicDetail(TopicStore.currentTopicId);
                        } catch (error) {
                            toast(error.message || '恢复失败', 'error');
                        }
                    });
                });
                list.querySelectorAll('[data-version-delete]').forEach(button => {
                    button.addEventListener('click', async () => {
                        try {
                            await api(`/api/topics/${TopicStore.currentTopicId}/versions/${button.dataset.versionDelete}`, { method: 'DELETE' });
                            toast('版本已删除', 'success');
                            await loadVersions();
                        } catch (error) {
                            toast(error.message || '删除失败', 'error');
                        }
                    });
                });
            } catch (error) {
                list.innerHTML = `<div class="ui-state ui-state-error"><span class="ui-state-description">${escapeText(error.message || '加载失败')}</span></div>`;
            }
        };
        modal.querySelector('#topicVersionSaveBtn').addEventListener('click', async () => {
            try {
                await api(`/api/topics/${TopicStore.currentTopicId}/versions`, { method: 'POST', json: {} });
                toast('已保存新版本', 'success');
                await loadVersions();
            } catch (error) {
                toast(error.message || '保存失败', 'error');
            }
        });
        await loadVersions();
    }

    async function openUsageForm() {
        const modal = ensureModalShell('topicUsageModal', '使用记录与学情录入', `
            <p class="topic-form-hint">记录一次使用（usage_count+1）并批量录入逐题学情；手工记录为追加式，可形成趋势。</p>
            <div class="topic-usage-list custom-scrollbar" id="topicUsageList"></div>`, `
            <button type="button" class="glass-btn" data-topic-modal-close="topicUsageModal">取消</button>
            <button type="button" class="glass-btn-primary rounded-xl" id="topicUsageSubmit">提交</button>`);
        openModal('topicUsageModal');
        const items = (TopicStore.detail && TopicStore.detail.items) || [];
        modal.querySelector('#topicUsageList').innerHTML = items.map(item => `
            <div class="topic-usage-row" data-question-id="${item.question_id}">
                <span class="topic-usage-seq">#${item.seq_num}</span>
                <span class="topic-usage-content">${escapeText(((item.question || {}).content || '').slice(0, 40))}</span>
                <label>正确率<input type="number" class="glass-input topic-usage-rate" min="0" max="1" step="0.05" placeholder="0-1"></label>
                <label>平均用时(s)<input type="number" class="glass-input topic-usage-seconds" min="0" placeholder="秒"></label>
                <label>样本数<input type="number" class="glass-input topic-usage-sample" min="0" value="0"></label>
            </div>`).join('') || '<div class="ui-state ui-state-empty"><span class="ui-state-description">专题内没有题目</span></div>';
        modal.querySelector('#topicUsageSubmit').addEventListener('click', async () => {
            const questionStats = [];
            modal.querySelectorAll('.topic-usage-row').forEach(row => {
                const rate = row.querySelector('.topic-usage-rate').value;
                const seconds = row.querySelector('.topic-usage-seconds').value;
                const sample = row.querySelector('.topic-usage-sample').value;
                if (!rate && !seconds) return;
                questionStats.push({
                    question_id: parseInt(row.dataset.questionId, 10),
                    correct_rate: rate === '' ? null : parseFloat(rate),
                    avg_seconds: seconds === '' ? null : parseInt(seconds, 10),
                    sample_size: parseInt(sample, 10) || 0
                });
            });
            if (!questionStats.length) {
                toast('请至少填写一题的学情数据', 'info');
                return;
            }
            try {
                await api(`/api/topics/${TopicStore.currentTopicId}/usage`, { method: 'POST', json: { question_stats: questionStats } });
                closeModal('topicUsageModal');
                toast('学情已记录', 'success');
                await loadTopicDetail(TopicStore.currentTopicId);
            } catch (error) {
                toast(error.message || '记录失败', 'error');
            }
        });
    }

    async function openLinkEditor() {
        const modal = ensureModalShell('topicLinksModal', '专题关联', `
            <div class="topic-link-form">
                <select id="topicLinkTarget" class="glass-select">
                    <option value="">选择目标专题</option>
                    ${TopicStore.topics.filter(t => t.id !== TopicStore.currentTopicId).map(t => `<option value="${t.id}">${escapeText(t.title)}</option>`).join('')}
                </select>
                <select id="topicLinkCode" class="glass-select">
                    ${Object.keys(LINK_LABELS).map(code => `<option value="${code}">${LINK_LABELS[code]}</option>`).join('')}
                </select>
                <button type="button" class="glass-btn-primary rounded-xl" id="topicLinkAddBtn">建立关联</button>
            </div>
            <div class="topic-link-list custom-scrollbar" id="topicLinkList"></div>`, '');
        openModal('topicLinksModal');
        const loadLinks = async () => {
            const list = modal.querySelector('#topicLinkList');
            try {
                const result = await api(`/api/topics/${TopicStore.currentTopicId}/links`);
                const links = result.data || [];
                list.innerHTML = links.length ? links.map(link => `
                    <div class="topic-link-item">
                        <span class="topic-link-code">${escapeText(LINK_LABELS[link.link_code] || link.link_code)}</span>
                        <span class="topic-link-direction">${link.direction === 'out' ? '→' : '←'}</span>
                        <span class="topic-link-other">${escapeText((link.other_topic || {}).title || '?')}</span>
                        <button type="button" class="glass-btn glass-btn-danger" data-link-delete="${link.id}">删除</button>
                    </div>`).join('') : '<div class="ui-state ui-state-empty"><span class="ui-state-description">还没有关联</span></div>';
                list.querySelectorAll('[data-link-delete]').forEach(button => {
                    button.addEventListener('click', async () => {
                        try {
                            await api(`/api/topic-links/${button.dataset.linkDelete}`, { method: 'DELETE' });
                            toast('关联已删除', 'success');
                            await loadLinks();
                        } catch (error) {
                            toast(error.message || '删除失败', 'error');
                        }
                    });
                });
            } catch (error) {
                list.innerHTML = `<div class="ui-state ui-state-error"><span class="ui-state-description">${escapeText(error.message || '加载失败')}</span></div>`;
            }
        };
        modal.querySelector('#topicLinkAddBtn').addEventListener('click', async () => {
            const target = parseInt(modal.querySelector('#topicLinkTarget').value, 10);
            if (!target) {
                toast('请选择目标专题', 'info');
                return;
            }
            try {
                await api(`/api/topics/${TopicStore.currentTopicId}/links`, {
                    method: 'POST',
                    json: { target_topic_id: target, link_code: modal.querySelector('#topicLinkCode').value }
                });
                toast('关联已建立', 'success');
                await loadLinks();
                await loadTopicDetail(TopicStore.currentTopicId);
            } catch (error) {
                toast(error.message || '建立关联失败', 'error');
            }
        });
        await loadLinks();
    }

    // ---------------- 复制 / 导出 / 导入 / 删除 ----------------

    async function duplicateCurrentTopic() {
        try {
            const result = await api(`/api/topics/${TopicStore.currentTopicId}/duplicate`, { method: 'POST', json: {} });
            toast('已复制专题', 'success');
            await loadTopicList();
            await selectTopic(result.data.id);
        } catch (error) {
            toast(error.message || '复制失败', 'error');
        }
    }

    async function exportCurrentTopic() {
        try {
            const result = await api(`/api/topics/${TopicStore.currentTopicId}/export`);
            const blob = new Blob([JSON.stringify(result.data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `${(result.data.topic || {}).title || 'topic'}.mathbank-topic.json`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            toast('已导出 JSON（本地文件交换）', 'success');
        } catch (error) {
            toast(error.message || '导出失败', 'error');
        }
    }

    function openImportModal() {
        const modal = ensureModalShell('topicImportModal', '导入专题 JSON', `
            <p class="topic-form-hint">按题干全文匹配回填 question_id；未匹配的题目进入 unresolved 列表人工关联。</p>
            <label for="topicImportFile" class="sr-only">选择导出的 JSON 文件</label>
            <input id="topicImportFile" type="file" accept=".json,application/json" class="glass-input">
            <div class="topic-import-result custom-scrollbar" id="topicImportResult"></div>`, '');
        openModal('topicImportModal');
        modal.querySelector('#topicImportFile').addEventListener('change', async (event) => {
            const file = event.target.files && event.target.files[0];
            if (!file) return;
            try {
                const text = await file.text();
                const payload = JSON.parse(text);
                const result = await api('/api/topics/import', { method: 'POST', json: payload.data ? payload : { data: payload } });
                const data = result.data || {};
                const resultBox = modal.querySelector('#topicImportResult');
                resultBox.innerHTML = `
                    <div class="ui-state ui-state-success"><strong class="ui-state-title">导入成功</strong>
                    <span class="ui-state-description">匹配 ${data.matched_count || 0} 题${data.unresolved && data.unresolved.length ? `，${data.unresolved.length} 题未匹配` : ''}</span></div>
                    ${data.unresolved && data.unresolved.length ? `<ul class="topic-import-unresolved">${data.unresolved.map(entry => `<li>#${entry.index} ${escapeText(entry.content_excerpt)}（${escapeText(entry.reason)}）</li>`).join('')}</ul>` : ''}`;
                toast('导入成功', 'success');
                await loadTopicList();
                await selectTopic(data.id);
            } catch (error) {
                modal.querySelector('#topicImportResult').innerHTML = `<div class="ui-state ui-state-error"><span class="ui-state-description">${escapeText(error.message || '导入失败')}</span></div>`;
                toast(error.message || '导入失败', 'error');
            }
        });
    }

    async function deleteCurrentTopic() {
        const topic = TopicStore.detail && TopicStore.detail.topic;
        if (!topic) return;
        if (!confirm(`确定删除专题「${topic.title}」吗？专题内的题目组合、分组、版本与关联都会删除（题目本身不受影响）。`)) return;
        try {
            await api(`/api/topics/${TopicStore.currentTopicId}`, { method: 'DELETE' });
            toast('专题已删除', 'success');
            TopicStore.currentTopicId = null;
            TopicStore.detail = null;
            renderDetailPanel();
            renderInspector();
            await loadTopicList();
        } catch (error) {
            toast(error.message || '删除失败', 'error');
        }
    }

    // ---------------- 移动端与入口 ----------------

    window.setTopicMobilePane = function (pane) {
        const body = document.querySelector('.topic-workspace-body');
        if (!body) return;
        const target = ['list', 'detail', 'inspector'].includes(pane) ? pane : 'list';
        body.dataset.mobilePane = target;
        document.querySelectorAll('[data-topic-pane]').forEach(button => {
            button.setAttribute('aria-selected', String(button.dataset.topicPane === target));
        });
    };

    window.onTopicSearchInput = function (value) {
        TopicStore.keyword = String(value || '').trim();
        clearTimeout(window.__topicSearchTimer);
        window.__topicSearchTimer = setTimeout(() => loadTopicList(), 250);
    };

    window.toggleTopicCreateMenu = toggleTopicCreateMenu;
    window.createTopicBlank = createTopicBlank;
    window.openTopicFromTagsModal = openTopicFromTagsModal;
    window.openTopicFromPaperModal = openTopicFromPaperModal;
    window.openTopicFromCurriculumModal = openTopicFromCurriculumModal;
    window.openAddToTopicModal = async function (questionId) {
        // 题库侧「加入专题」入口：确保列表已加载，然后打开选题器并预选该题。
        if (!TopicStore.topics.length) await loadTopicList();
        if (!TopicStore.currentTopicId) {
            toast('请先选择或创建一个专题', 'info');
            const first = TopicStore.topics[0];
            if (first) await selectTopic(first.id);
            else {
                await createTopicBlank();
                if (!TopicStore.currentTopicId) return;
            }
        }
        await openQuestionPicker();
        TopicStore.pickerSelection.add(parseInt(questionId, 10));
        const count = document.getElementById('topicPickerCount');
        if (count) count.textContent = `已选 ${TopicStore.pickerSelection.size} 题`;
    };

    async function renderTopicWorkspace() {
        debug.debug('renderTopicWorkspace');
        await Promise.all([loadConfig(), loadTopicList()]);
        if (TopicStore.currentTopicId) {
            const exists = TopicStore.topics.some(t => t.id === TopicStore.currentTopicId);
            if (exists) await loadTopicDetail(TopicStore.currentTopicId);
            else {
                TopicStore.currentTopicId = null;
                TopicStore.detail = null;
                renderDetailPanel();
                renderInspector();
            }
        } else {
            renderDetailPanel();
            renderInspector();
        }
    }
    window.renderTopicWorkspace = renderTopicWorkspace;

    debug.debug('topic module loaded');
})();
