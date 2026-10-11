/**
 * tags.js — 人教A版2019 分类（标签）体系选择器
 *
 * 提供三类多值标签的录入与回显：
 *   1. 教材章节：册 → 章 → 节 → 小节 四级级联，可添加多个节点（chips）
 *   2. 宏观数学思想：受控词表多选（chips 点选切换）
 *   3. 中观解题策略：受控词表多选（chips 点选切换），顺序同思想
 *   4. 微观解题方法：自由文本 + 记忆复用 + 参考目录卡片
 *   5. 功能：单选（易错题 / 创新题 / 典型题，可空）
 *
 * 对外接口：window.MathBankTags.init() / getSelection() / setSelection() / clear()
 */
(function () {
    'use strict';

    const State = {
        ready: false,
        tree: null,
        schema: null,
        chapters: [],   // 已选章节节点编码
        thoughts: [],   // 已选思想方法编码
        strategies: [], // 已选解题策略编码（中观）
        micros: [],     // 已选微观解题方法（自由文本）
        functionCode: '',
        dirty: false    // 用户是否主动改动过标签（用于区分"回显"与"编辑"）
    };

    function $(id) {
        return document.getElementById(id);
    }

    function escapeText(value) {
        if (window.MathBankSafe && typeof window.MathBankSafe.escapeText === 'function') {
            return window.MathBankSafe.escapeText(value);
        }
        return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    function fill(select, items, placeholder, selected) {
        if (!select) return;
        select.innerHTML = '';
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = placeholder || '--';
        select.appendChild(empty);
        items.forEach((item) => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            select.appendChild(option);
        });
        if (selected) select.value = selected;
        select.disabled = items.length === 0;
    }

    function findBook(code) {
        return (State.tree && State.tree.books || []).find((book) => book.code === code) || null;
    }

    function findChapter(book, code) {
        if (!book) return null;
        return (book.chapters || []).find((chapter) => chapter.code === code) || null;
    }

    function findSection(chapter, code) {
        if (!chapter) return null;
        return (chapter.sections || []).find((section) => section.code === code) || null;
    }

    function findSubsection(section, code) {
        if (!section) return null;
        return (section.subsections || []).find((sub) => sub.code === code) || null;
    }

    function currentSelectionCode() {
        const book = findBook($('tagBookSelect') && $('tagBookSelect').value);
        const chapter = findChapter(book, $('tagChapterSelect') && $('tagChapterSelect').value);
        const section = findChapter ? findSection(chapter, $('tagSectionSelect') && $('tagSectionSelect').value) : null;
        const subsection = findSubsection(section, $('tagSubsectionSelect') && $('tagSubsectionSelect').value);
        if (subsection) return subsection.code;
        if (section) return section.code;
        if (chapter) return chapter.code;
        if (book) return book.code;
        return '';
    }

    function nodePath(code) {
        const books = (State.tree && State.tree.books) || [];
        for (const book of books) {
            if (book.code === code) return book.name;
            for (const chapter of book.chapters || []) {
                if (chapter.code === code) return chapter.path;
                for (const section of chapter.sections || []) {
                    if (section.code === code) return section.path;
                    for (const sub of section.subsections || []) {
                        if (sub.code === code) return sub.path;
                    }
                }
            }
        }
        return code;
    }

    function renderChapterChips() {
        const container = $('tagChapterChips');
        if (!container) return;
        container.innerHTML = '';
        if (!State.chapters.length) {
            const hint = document.createElement('span');
            hint.className = 'text-[10px] text-slate-400';
            hint.textContent = '未选择章节节点（新体系要求至少 1 个）';
            container.appendChild(hint);
            return;
        }
        State.chapters.forEach((code) => {
            const chip = document.createElement('span');
            chip.className = 'inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full bg-brand-50 text-brand-700 border border-brand-200';
            chip.innerHTML = '<i class="fa-solid fa-location-dot"></i><span>' + escapeText(nodePath(code)) + '</span>';
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'ml-0.5 text-brand-400 hover:text-brand-700';
            remove.innerHTML = '<i class="fa-solid fa-xmark"></i>';
            remove.addEventListener('click', () => {
                State.chapters = State.chapters.filter((item) => item !== code);
                State.dirty = true;
                renderChapterChips();
            });
            chip.appendChild(remove);
            container.appendChild(chip);
        });
    }

    function renderThoughtChips() {
        const container = $('tagThoughtChips');
        if (!container || !State.schema) return;
        const dimension = (State.schema.dimensions || []).find((item) => item.key === 'thought_method');
        container.innerHTML = '';
        // 展示顺序 = 词表声明顺序（课程标准：转化与化归 → … → 符号化）。
        (dimension && dimension.values || []).forEach((value) => {
            const active = State.thoughts.indexOf(value.code) !== -1;
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.textContent = value.label;
            chip.title = value.hint || value.label;
            chip.className = active
                ? 'text-[11px] px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-300'
                : 'text-[11px] px-2 py-1 rounded-full bg-white text-slate-500 border border-slate-200 hover:border-emerald-300';
            chip.addEventListener('click', () => {
                const index = State.thoughts.indexOf(value.code);
                if (index === -1) {
                    if (State.thoughts.length >= 3) {
                        if (typeof showToast === 'function') showToast('宏观数学思想建议不超过 3 个，请先取消一个再选择。', 'info');
                        return;
                    }
                    State.thoughts.push(value.code);
                } else {
                    State.thoughts.splice(index, 1);
                }
                State.dirty = true;
                renderThoughtChips();
            });
            container.appendChild(chip);
        });
    }

    function renderStrategyChips() {
        const container = $('tagStrategyChips');
        if (!container || !State.schema) return;
        const dimension = (State.schema.dimensions || []).find((item) => item.key === 'solve_strategy');
        container.innerHTML = '';
        (dimension && dimension.values || []).forEach((value) => {
            const active = State.strategies.indexOf(value.code) !== -1;
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.textContent = value.label;
            chip.title = value.hint || value.label;
            chip.className = active
                ? 'text-[11px] px-2 py-1 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-300'
                : 'text-[11px] px-2 py-1 rounded-full bg-white text-slate-500 border border-slate-200 hover:border-indigo-300';
            chip.addEventListener('click', () => {
                const index = State.strategies.indexOf(value.code);
                if (index === -1) State.strategies.push(value.code);
                else State.strategies.splice(index, 1);
                State.dirty = true;
                renderStrategyChips();
            });
            container.appendChild(chip);
        });
    }

    function renderMicroChips() {
        const container = $('tagMicroChips');
        if (!container) return;
        container.innerHTML = '';
        if (!State.micros.length) {
            const hint = document.createElement('span');
            hint.className = 'text-[10px] text-slate-400';
            hint.textContent = '暂无微观方法；在上方输入后回车添加（可从记忆或目录卡片中选）。';
            container.appendChild(hint);
            return;
        }
        State.micros.forEach((label) => {
            const chip = document.createElement('span');
            chip.className = 'inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200';
            chip.innerHTML = '<i class="fa-solid fa-microchip text-[9px]"></i><span>' + escapeText(label) + '</span>';
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'ml-0.5 text-amber-400 hover:text-amber-700';
            remove.innerHTML = '<i class="fa-solid fa-xmark"></i>';
            remove.addEventListener('click', () => {
                State.micros = State.micros.filter((item) => item !== label);
                State.dirty = true;
                renderMicroChips();
            });
            chip.appendChild(remove);
            container.appendChild(chip);
        });
    }

    function addMicroFromInputValue(raw) {
        const parts = String(raw || '').split(/[,，]/).map((item) => item.trim()).filter(Boolean);
        if (!parts.length) return;
        parts.forEach((label) => {
            if (State.micros.indexOf(label) === -1) State.micros.push(label);
        });
        State.dirty = true;
        renderMicroChips();
    }

    function addMicroFromInput() {
        const input = $('tagMicroInput');
        if (!input) return;
        addMicroFromInputValue(input.value);
        input.value = '';
    }

    /**
     * 目录卡片：把 micro_method_vocab.json 的 L1 模块 / L2 方法族 / L3 技法
     * 渲染成一棵可折叠的目录，点任意一项即加入「微观解题方法」，支持按关键字检索。
     * 与记忆面板互补：记忆是「我用过什么」，目录是「参考有什么」。
     */
    let microCatalogData = null;
    let microCatalogQuery = '';

    function loadMicroCatalog() {
        if (microCatalogData) return Promise.resolve(microCatalogData);
        return fetch('/api/config/micro-method-vocab')
            .then((res) => (res.ok ? res.json() : null))
            .then((vocab) => {
                microCatalogData = vocab && Array.isArray(vocab.modules) ? vocab.modules : [];
                microVocabSuggestions = null;   // 让记忆面板的建议列表同步复用最新数据
                loadMicroSuggestions();
                return microCatalogData;
            })
            .catch(() => []);
    }

    function addMicroLabel(label) {
        const clean = String(label || '').trim();
        if (!clean || State.micros.indexOf(clean) !== -1) return;
        State.micros.push(clean);
        State.dirty = true;
        renderMicroChips();
    }

    function microCatalogMatches(haystack) {
        const query = String(microCatalogQuery || '').trim().toLowerCase();
        if (!query) return true;
        return String(haystack || '').toLowerCase().indexOf(query) !== -1;
    }

    function renderMicroCatalog() {
        const body = $('tagMicroCatalogBody');
        if (!body) return;
        body.innerHTML = '';
        const modules = microCatalogData || [];
        let hitCount = 0;

        modules.forEach((module) => {
            const families = (module.families || []).filter((family) => {
                const techniques = family.techniques || [];
                return microCatalogMatches(module.name) || microCatalogMatches(family.name)
                    || techniques.some((technique) => microCatalogMatches(technique));
            });
            if (!families.length) return;
            hitCount += families.length;

            const details = document.createElement('details');
            details.className = 'mb-1 rounded-lg border border-amber-100 bg-white/70';
            details.open = Boolean(microCatalogQuery);
            const moduleName = escapeText(module.name || '未命名模块');
            details.innerHTML = `<summary class="cursor-pointer list-none px-2 py-1.5 text-[11px] font-semibold text-amber-800 flex items-center gap-1.5">
                <i class="fa-solid fa-folder-open text-amber-400 text-[10px]"></i><span>${moduleName}</span>
                <span class="text-[9px] text-slate-400">（${families.length}）</span></summary>`;

            families.forEach((family) => {
                const familyBox = document.createElement('div');
                familyBox.className = 'px-2 pb-1.5';
                const familyName = String(family.name || '').trim();
                const techniques = (family.techniques || []).filter(
                    (technique) => microCatalogMatches(technique) || microCatalogMatches(familyName)
                );
                if (familyName) {
                    const header = document.createElement('div');
                    header.className = 'mt-1 mb-1 flex items-center gap-1.5';
                    header.innerHTML = `<span class="text-[10px] font-semibold text-slate-600">${escapeText(familyName)}</span>`;
                    const pickFamily = document.createElement('button');
                    pickFamily.type = 'button';
                    pickFamily.className = 'text-[9px] px-1.5 py-0.5 rounded border border-amber-200 text-amber-700 hover:bg-amber-50';
                    pickFamily.textContent = '选方法族';
                    pickFamily.addEventListener('click', () => addMicroLabel(familyName));
                    header.appendChild(pickFamily);
                    familyBox.appendChild(header);
                }
                if (techniques.length) {
                    const list = document.createElement('div');
                    list.className = 'flex flex-wrap gap-1';
                    techniques.forEach((technique) => {
                        const label = familyName ? familyName + '·' + technique : technique;
                        const chip = document.createElement('button');
                        chip.type = 'button';
                        chip.className = 'text-[10px] px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-600 hover:border-amber-300 hover:text-amber-700';
                        chip.textContent = technique;
                        chip.title = '点击加入：' + label;
                        chip.addEventListener('click', () => addMicroLabel(label));
                        list.appendChild(chip);
                    });
                    familyBox.appendChild(list);
                }
                details.appendChild(familyBox);
            });
            body.appendChild(details);
        });

        if (!hitCount) {
            const empty = document.createElement('p');
            empty.className = 'px-1 py-3 text-[11px] text-slate-400';
            empty.textContent = '目录中没有匹配项，可在上方输入框自行输入技法名称后回车添加。';
            body.appendChild(empty);
        }
    }

    function toggleMicroCatalog() {
        const card = $('tagMicroCatalogCard');
        if (!card) return;
        const willShow = card.classList.contains('hidden');
        if (willShow) {
            card.classList.remove('hidden');
            loadMicroCatalog().then(() => renderMicroCatalog());
        } else {
            card.classList.add('hidden');
        }
    }

    function buildFunctionSelect() {
        const select = $('tagFunctionSelect');
        if (!select || !State.schema) return;
        const dimension = (State.schema.dimensions || []).find((item) => item.key === 'function_tag');
        fill(select, (dimension && dimension.values || []).map((value) => ({
            value: value.code, label: value.label
        })), '无（普通题）', State.functionCode || '');
    }

    function buildBookSelect() {
        const books = (State.tree && State.tree.books) || [];
        fill($('tagBookSelect'), books.map((book) => ({ value: book.code, label: book.name })), '-- 选择册次 --');
        fill($('tagChapterSelect'), [], '-- 先选册次 --');
        fill($('tagSectionSelect'), [], '-- 先选章 --');
        fill($('tagSubsectionSelect'), [], '-- 先选节 --');
    }

    function bindCascade() {
        const bookSelect = $('tagBookSelect');
        const chapterSelect = $('tagChapterSelect');
        const sectionSelect = $('tagSectionSelect');
        const subsectionSelect = $('tagSubsectionSelect');

        if (bookSelect) {
            bookSelect.addEventListener('change', () => {
                const book = findBook(bookSelect.value);
                fill(chapterSelect, (book && book.chapters || []).map((chapter) => ({
                    value: chapter.code, label: '第' + chapter.no + '章 ' + chapter.name
                })), '-- 选择章 --');
                fill(sectionSelect, [], '-- 先选章 --');
                fill(subsectionSelect, [], '-- 先选节 --');
            });
        }
        if (chapterSelect) {
            chapterSelect.addEventListener('change', () => {
                const book = findBook(bookSelect.value);
                const chapter = findChapter(book, chapterSelect.value);
                fill(sectionSelect, (chapter && chapter.sections || []).map((section) => ({
                    value: section.code,
                    label: section.label + ' ' + section.name + (section.optional ? '（选学）' : '')
                })), '-- 选择节（可留空挂整章）--');
                fill(subsectionSelect, [], '-- 先选节 --');
            });
        }
        if (sectionSelect) {
            sectionSelect.addEventListener('change', () => {
                const book = findBook(bookSelect.value);
                const chapter = findChapter(book, chapterSelect.value);
                const section = findSection(chapter, sectionSelect.value);
                fill(subsectionSelect, (section && section.subsections || []).map((sub) => ({
                    value: sub.code, label: sub.label + ' ' + sub.name
                })), '-- 选择小节（可留空挂整节）--');
            });
        }
        const addButton = $('tagAddChapterBtn');
        if (addButton) {
            addButton.addEventListener('click', () => {
                const code = currentSelectionCode();
                if (!code) {
                    if (typeof showToast === 'function') showToast('请先选择册次或更细的章节节点', 'info');
                    return;
                }
                if (State.chapters.indexOf(code) === -1) State.chapters.push(code);
                State.dirty = true;
                renderChapterChips();
            });
        }
    }

    // 微观解题方法参考目录（/api/config/micro-method-vocab）缓存。
    let microVocabSuggestions = null;

    function loadMicroSuggestions() {
        if (microVocabSuggestions) return Promise.resolve(microVocabSuggestions);
        return fetch('/api/config/micro-method-vocab')
            .then((res) => (res.ok ? res.json() : null))
            .then((vocab) => {
                const suggestions = [];
                (vocab && vocab.modules || []).forEach((module) => {
                    (module.families || []).forEach((family) => {
                        const familyName = family.name || '';
                        if (familyName) suggestions.push({ label: familyName, hint: module.name });
                        (family.techniques || []).forEach((technique) => {
                            suggestions.push({
                                label: familyName ? familyName + '·' + technique : technique,
                                hint: module.name
                            });
                        });
                    });
                });
                microVocabSuggestions = suggestions;
                return suggestions;
            })
            .catch(() => []);
    }

    async function init() {
        if (State.ready) return true;
        try {
            const [schema, tree] = await Promise.all([
                fetch('/api/config/tag-schema').then((res) => (res.ok ? res.json() : null)),
                fetch('/api/config/curriculum-tree/A').then((res) => (res.ok ? res.json() : null))
            ]);
            State.schema = schema;
            State.tree = tree;
            buildBookSelect();
            bindCascade();
            renderThoughtChips();
            renderStrategyChips();
            buildFunctionSelect();
            renderChapterChips();
            renderMicroChips();
            const microInput = $('tagMicroInput');
            if (microInput) {
                microInput.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' || event.key === ',' || event.key === '，') {
                        event.preventDefault();
                        addMicroFromInput();
                    }
                });
            }
            const microAddBtn = $('tagMicroAddBtn');
            if (microAddBtn) microAddBtn.addEventListener('click', addMicroFromInput);
            const catalogBtn = $('tagMicroCatalogBtn');
            if (catalogBtn) catalogBtn.addEventListener('click', toggleMicroCatalog);
            const catalogSearch = $('tagMicroCatalogSearch');
            if (catalogSearch) {
                catalogSearch.addEventListener('input', () => {
                    microCatalogQuery = catalogSearch.value || '';
                    if (microCatalogData) renderMicroCatalog();
                });
                catalogSearch.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        addMicroFromInputValue(catalogSearch.value);
                        catalogSearch.value = '';
                        microCatalogQuery = '';
                        if (microCatalogData) renderMicroCatalog();
                    }
                });
            }
            // 记忆复用：自定义标签与微观方法共用同一个记忆面板组件。
            if (window.MathBankTagMemory && typeof window.MathBankTagMemory.attach === 'function') {
                const editTags = document.getElementById('editTags');
                if (editTags) window.MathBankTagMemory.attach(editTags, { dim: 'custom' });
                const microInput = $('tagMicroInput');
                if (microInput) {
                    loadMicroSuggestions();
                    window.MathBankTagMemory.attach(microInput, {
                        dim: 'micro',
                        suggestionTitle: '目录建议（微观解题方法）',
                        getSuggestions: () => microVocabSuggestions || []
                    });
                }
            }
            const functionSelect = $('tagFunctionSelect');
            if (functionSelect) {
                functionSelect.addEventListener('change', () => {
                    State.functionCode = functionSelect.value || '';
                    State.dirty = true;
                });
            }
            State.ready = true;
            return true;
        } catch (error) {
            console.warn('[tags] 分类体系配置加载失败', error);
            return false;
        }
    }

    function getSelection() {
        return {
            chapter_codes: State.chapters.slice(),
            thought_codes: State.thoughts.slice(),
            strategy_codes: State.strategies.slice(),
            micro_methods: State.micros.slice(),
            function_code: State.functionCode || '',
            custom_tags: document.getElementById('editTags')
                ? String(document.getElementById('editTags').value || '').trim()
                : ''
        };
    }

    function setSelection(tagCodes) {
        if (!tagCodes || typeof tagCodes !== 'object') return;
        const chapters = Array.isArray(tagCodes.chapter) ? tagCodes.chapter : [];
        const thoughts = Array.isArray(tagCodes.thought) ? tagCodes.thought : [];
        const strategies = Array.isArray(tagCodes.strategy) ? tagCodes.strategy : [];
        const micros = Array.isArray(tagCodes.micro) ? tagCodes.micro : [];
        const functions = Array.isArray(tagCodes.function) ? tagCodes.function : [];
        State.chapters = chapters.filter(Boolean);
        State.thoughts = thoughts.filter(Boolean);
        State.strategies = strategies.filter(Boolean);
        State.micros = micros.filter(Boolean);
        State.functionCode = functions.length ? functions[0] : '';
        State.dirty = false;  // 回显现有标签不算"改动"
        if (State.ready) {
            renderChapterChips();
            renderThoughtChips();
            renderStrategyChips();
            renderMicroChips();
            const functionSelect = $('tagFunctionSelect');
            if (functionSelect) functionSelect.value = State.functionCode;
        } else {
            init().then(() => {
                renderChapterChips();
                renderThoughtChips();
                renderStrategyChips();
                renderMicroChips();
                const functionSelect = $('tagFunctionSelect');
                if (functionSelect) functionSelect.value = State.functionCode;
            });
        }
    }

    function clear() {
        State.chapters = [];
        State.thoughts = [];
        State.strategies = [];
        State.micros = [];
        State.functionCode = '';
        State.dirty = true;
        renderChapterChips();
        renderThoughtChips();
        renderStrategyChips();
        renderMicroChips();
        const functionSelect = $('tagFunctionSelect');
        if (functionSelect) functionSelect.value = '';
        ['tagBookSelect', 'tagChapterSelect', 'tagSectionSelect', 'tagSubsectionSelect'].forEach((id) => {
            const select = $(id);
            if (select) select.value = '';
        });
        const microInput = $('tagMicroInput');
        if (microInput) microInput.value = '';
    }

    function isDirty() {
        return State.dirty === true;
    }

    window.MathBankTags = { init, getSelection, setSelection, clear, isDirty, state: State };
})();
