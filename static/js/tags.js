/**
 * tags.js — 人教A版2019 分类（标签）体系选择器
 *
 * 提供三类多值标签的录入与回显：
 *   1. 教材章节：册 → 章 → 节 → 小节 四级级联，可添加多个节点（chips）
 *   2. 数学思想方法：受控词表多选（chips 点选切换）
 *   3. 功能：单选（易错题 / 创新题，可空）
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
        (dimension && dimension.values || []).forEach((value) => {
            const active = State.thoughts.indexOf(value.code) !== -1;
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.textContent = value.label;
            chip.className = active
                ? 'text-[11px] px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-300'
                : 'text-[11px] px-2 py-1 rounded-full bg-white text-slate-500 border border-slate-200 hover:border-emerald-300';
            chip.addEventListener('click', () => {
                const index = State.thoughts.indexOf(value.code);
                if (index === -1) {
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
            buildFunctionSelect();
            renderChapterChips();
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
        const functions = Array.isArray(tagCodes.function) ? tagCodes.function : [];
        State.chapters = chapters.filter(Boolean);
        State.thoughts = thoughts.filter(Boolean);
        State.functionCode = functions.length ? functions[0] : '';
        State.dirty = false;  // 回显现有标签不算"改动"
        if (State.ready) {
            renderChapterChips();
            renderThoughtChips();
            const functionSelect = $('tagFunctionSelect');
            if (functionSelect) functionSelect.value = State.functionCode;
        } else {
            init().then(() => {
                renderChapterChips();
                renderThoughtChips();
                const functionSelect = $('tagFunctionSelect');
                if (functionSelect) functionSelect.value = State.functionCode;
            });
        }
    }

    function clear() {
        State.chapters = [];
        State.thoughts = [];
        State.functionCode = '';
        State.dirty = true;
        renderChapterChips();
        renderThoughtChips();
        const functionSelect = $('tagFunctionSelect');
        if (functionSelect) functionSelect.value = '';
        ['tagBookSelect', 'tagChapterSelect', 'tagSectionSelect', 'tagSubsectionSelect'].forEach((id) => {
            const select = $(id);
            if (select) select.value = '';
        });
    }

    function isDirty() {
        return State.dirty === true;
    }

    window.MathBankTags = { init, getSelection, setSelection, clear, isDirty, state: State };
})();
