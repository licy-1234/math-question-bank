// Sidebar Pagination & Sorting Global State
let currentBankPage = 1;
let currentDraftPage = 1;
const PAGE_LIMIT = 20;
let bankQuestionsLoadController = null;
let bankQuestionsLoadSequence = 0;
let bankQuestionsRetryTimer = null;

// A subdivision always carries its complete curriculum path. Legacy category
// fields and free-form tags remain separate, and selections belong to a version.
let subKnowledgeCatalog = [];
let subKnowledgeCatalogRequest = 0;
let editorSubKnowledgeByVersion = {};
let editorClassificationByVersion = {};
function normalizeSubKnowledge(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value.filter(item => item && typeof item === 'object' && !Array.isArray(item))
        .map(item => ({ compulsory: String(item.compulsory || '').trim(),
            chapter: String(item.chapter || '').trim(), knowledge: String(item.knowledge || '').trim() }))
        .filter(item => item.compulsory && item.chapter && item.knowledge)
        .filter(item => { const key = JSON.stringify(item); if (seen.has(key)) return false; seen.add(key); return true; });
}
function subKnowledgeSignature(value) {
    return JSON.stringify(normalizeSubKnowledge(value).map(item => JSON.stringify(item)).sort());
}
function subKnowledgeVersionsSignature(value) {
    return JSON.stringify(Object.entries(value || {}).map(([version, paths]) => [version, subKnowledgeSignature(paths)])
        .filter(([, signature]) => signature !== '[]').sort((left, right) => left[0].localeCompare(right[0])));
}
function subKnowledgePathLabel(path) {
    return [path.compulsory, path.chapter, path.knowledge].join(' / ');
}
function getSubKnowledgeSelection(kind) {
    const container = document.getElementById(`${kind}SubKnowledgeOptions`);
    if (!container) return [];
    const paths = Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(input => {
        try { return JSON.parse(input.value); } catch (_) { return null; }
    });
    return normalizeSubKnowledge(paths);
}
function getEditorSubKnowledge() { return getSubKnowledgeSelection('edit'); }
function getFilterSubKnowledge() { return getSubKnowledgeSelection('filter'); }
function directorySubKnowledgePaths() {
    const result = [];
    Object.entries(categoryTree || {}).forEach(([compulsory, chapters]) => {
        Object.entries(chapters || {}).forEach(([chapter, values]) => {
            if (Array.isArray(values)) values.forEach(knowledge => result.push({compulsory, chapter, knowledge}));
        });
    });
    return normalizeSubKnowledge(result);
}
function refreshSubKnowledgeSummary(kind) {
    const summary = document.getElementById(`${kind}SubKnowledgeSummary`);
    if (!summary) return;
    const count = getSubKnowledgeSelection(kind).length;
    summary.textContent = count ? `细分知识点（已选 ${count} 项）` : '细分知识点（可多选）';
}
function searchSubKnowledgeOptions(kind, value) {
    const container = document.getElementById(`${kind}SubKnowledgeOptions`);
    const query = String(value || '').trim().toLowerCase();
    if (container) Array.from(container.querySelectorAll('label')).forEach(label => {
        label.hidden = Boolean(query) && !label.textContent.toLowerCase().includes(query);
    });
}
function renderSubKnowledgeOptions(kind, selected = null) {
    const container = document.getElementById(`${kind}SubKnowledgeOptions`);
    if (!container) return;
    const paths = normalizeSubKnowledge(selected === null ? getSubKnowledgeSelection(kind) : selected);
    const checked = new Set(paths.map(path => JSON.stringify(path)));
    const candidates = normalizeSubKnowledge([...paths, ...directorySubKnowledgePaths(), ...subKnowledgeCatalog]);
    container.replaceChildren();
    candidates.forEach(path => {
        const label = document.createElement('label');
        label.className = 'sub-knowledge-option';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = JSON.stringify(path);
        input.checked = checked.has(input.value);
        input.addEventListener('change', () => {
            refreshSubKnowledgeSummary(kind);
            if (kind === 'edit') {
                if (window.activeCurriculumVersion) editorSubKnowledgeByVersion[window.activeCurriculumVersion] = getEditorSubKnowledge();
                renderEditorPaperMeta();
                refreshEditorFeedback();
            } else if (kind === 'filter') {
                currentBankPage = currentDraftPage = 1;
                updateBankFilterSummary();
                if (activeSidebarTab === 'bank') loadQuestions(); else loadDrafts();
            } else if (kind === 'paper' && typeof window.onPaperFilterChange === 'function') {
                window.onPaperFilterChange('sub_knowledge', getSubKnowledgeSelection(kind));
            }
        });
        const text = document.createElement('span');
        text.textContent = subKnowledgePathLabel(path);
        label.append(input, text);
        container.appendChild(label);
    });
    if (!candidates.length) {
        const empty = document.createElement('p');
        empty.className = 'sub-knowledge-empty';
        empty.textContent = '当前目录暂无候选，可填写完整路径添加。';
        container.appendChild(empty);
    }
    refreshSubKnowledgeSummary(kind);
    searchSubKnowledgeOptions(kind, document.getElementById(`${kind}SubKnowledgeSearch`)?.value);
}
function setEditorSubKnowledge(value) {
    const paths = normalizeSubKnowledge(value);
    const version = window.activeCurriculumVersion || window.editorCurriculumVersion;
    if (version) editorSubKnowledgeByVersion[version] = paths;
    renderSubKnowledgeOptions('edit', paths);
}
function resetEditorSubKnowledge() {
    editorSubKnowledgeByVersion = {};
    editorClassificationByVersion = {};
    window.editorCurriculumVersion = window.activeCurriculumVersion || '';
    setEditorSubKnowledge([]);
}
function hydrateEditorSubKnowledge(record, isDraft = false) {
    editorSubKnowledgeByVersion = {};
    editorClassificationByVersion = isDraft && record?.classification_by_version ? {...record.classification_by_version} : {};
    if (isDraft && record && record.sub_knowledge_by_version) {
        Object.entries(record.sub_knowledge_by_version).forEach(([version, paths]) => {
            editorSubKnowledgeByVersion[version] = normalizeSubKnowledge(paths);
        });
    }
    window.editorCurriculumVersion = isDraft ? (window.activeCurriculumVersion || '')
        : (record?.active_version_code || window.activeCurriculumVersion || '');
    if (window.editorCurriculumVersion) editorClassificationByVersion[window.editorCurriculumVersion] = {
        category_compulsory: document.getElementById('editCompulsory')?.value || '',
        category_chapter: document.getElementById('editChapter')?.value || '',
        category_knowledge: document.getElementById('editKnowledge')?.value || ''
    };
    setEditorSubKnowledge(isDraft ? subKnowledgeForDraft(record) : record?.sub_knowledge || []);
}
function editorSubKnowledgeVersionSnapshot() {
    const result = {};
    Object.entries(editorSubKnowledgeByVersion).forEach(([version, paths]) => { result[version] = normalizeSubKnowledge(paths); });
    const version = window.activeCurriculumVersion || window.editorCurriculumVersion;
    if (version) result[version] = getEditorSubKnowledge();
    return result;
}
function editorClassificationVersionSnapshot() {
    const result = JSON.parse(JSON.stringify(editorClassificationByVersion));
    const version = window.editorCurriculumVersion || window.activeCurriculumVersion;
    if (version) result[version] = {
        category_compulsory: document.getElementById('editCompulsory')?.value || '',
        category_chapter: document.getElementById('editChapter')?.value || '',
        category_knowledge: document.getElementById('editKnowledge')?.value || ''
    };
    return result;
}
function draftClassification(draft) {
    const version = window.activeCurriculumVersion || '';
    return draft?.classification_by_version?.[version]
        || (!draft?.version_code || draft.version_code === version ? draft : {});
}
function addEditorSubKnowledge() {
    const fields = ['Compulsory', 'Chapter', 'Knowledge'].map(key => document.getElementById(`editSubKnowledgeCustom${key}`));
    if (fields.some(field => !field || !field.value.trim())) {
        showToast('请填写细分知识点的学段、章节和小节完整路径。', 'info');
        fields.find(field => field && !field.value.trim())?.focus();
        return;
    }
    const path = {compulsory: fields[0].value, chapter: fields[1].value, knowledge: fields[2].value};
    setEditorSubKnowledge([...getEditorSubKnowledge(), path]);
    fields.forEach(field => { field.value = ''; });
    const picker = document.getElementById('editSubKnowledgePicker');
    if (picker) picker.open = true;
    renderEditorPaperMeta();
    refreshEditorFeedback();
}
function subKnowledgeForDraft(draft) {
    const version = window.activeCurriculumVersion || '';
    const stored = draft && draft.sub_knowledge_by_version;
    if (stored && typeof stored === 'object' && version) return normalizeSubKnowledge(stored[version]);
    return draft && draft.version_code && draft.version_code === version ? normalizeSubKnowledge(draft.sub_knowledge) : [];
}
function matchesQuestionCurriculumFilters(item, filters) {
    const path = {compulsory: item.category_compulsory || '', chapter: item.category_chapter || '', knowledge: item.category_knowledge || ''};
    return ['compulsory', 'chapter', 'knowledge'].every(key => !filters[key] || path[key] === filters[key]);
}
function renderSubKnowledgeBadges(value, compact = false) {
    const paths = normalizeSubKnowledge(value);
    if (!paths.length) return '';
    const shown = compact ? paths.slice(0, 2) : paths;
    const badges = shown.map(path => `<span class="sub-knowledge-badge" title="${window.MathBankSafe.escapeAttribute(subKnowledgePathLabel(path))}">细分：${window.MathBankSafe.escapeText(subKnowledgePathLabel(path))}</span>`).join('');
    const more = compact && paths.length > shown.length ? `<span class="sub-knowledge-badge" title="${window.MathBankSafe.escapeAttribute(paths.map(subKnowledgePathLabel).join('；'))}">+${paths.length - shown.length}</span>` : '';
    return `<div class="sub-knowledge-badges">${badges}${more}</div>`;
}
async function loadSubKnowledgeCatalog() {
    const request = ++subKnowledgeCatalogRequest;
    const response = await fetch('/api/config/sub-knowledge');
    if (!response.ok) throw new Error('细分知识点目录加载失败');
    const data = await response.json();
    if (request !== subKnowledgeCatalogRequest) return null;
    const previousVersion = window.activeCurriculumVersion || window.editorCurriculumVersion;
    const nextVersion = typeof data.version_code === 'string' ? data.version_code : '';
    if (!nextVersion) throw new Error('教材版本信息缺失');
    const versionChanged = Boolean(previousVersion && previousVersion !== nextVersion);
    const editorWasModified = typeof isEditorModified === 'function' && isEditorModified();
    if (previousVersion) editorSubKnowledgeByVersion[previousVersion] = getEditorSubKnowledge();
    window.activeCurriculumVersion = nextVersion;
    subKnowledgeCatalog = normalizeSubKnowledge(data.sub_knowledge);
    if (versionChanged) {
        setEditorSubKnowledge(editorSubKnowledgeByVersion[nextVersion] || []);
        // A clean question may now reload its new-version mirror. Preserve a
        // dirty session and require its original version before saving it.
        if (!editorWasModified && originalQuestionState) {
            originalQuestionState.sub_knowledge = getEditorSubKnowledge();
            originalQuestionState.sub_knowledge_by_version = editorSubKnowledgeVersionSnapshot();
        }
        renderSubKnowledgeOptions('filter', []);
        currentBankPage = currentDraftPage = 1;
        if (window.PaperStore) window.PaperStore.filters.sub_knowledge = [];
    } else {
        renderSubKnowledgeOptions('edit');
        renderSubKnowledgeOptions('filter');
    }
    if (!window.editorCurriculumVersion) window.editorCurriculumVersion = nextVersion;
    if (window.PaperStore) renderSubKnowledgeOptions('paper', window.PaperStore.filters.sub_knowledge || []);
    return {...data, version_changed: versionChanged};
}
Object.assign(window, {normalizeSubKnowledge, subKnowledgeSignature, subKnowledgeVersionsSignature, subKnowledgePathLabel, getEditorSubKnowledge,
    getFilterSubKnowledge, getSubKnowledgeSelection, renderSubKnowledgeOptions, setEditorSubKnowledge,
    resetEditorSubKnowledge, hydrateEditorSubKnowledge, editorSubKnowledgeVersionSnapshot,
    editorClassificationVersionSnapshot, draftClassification,
    searchSubKnowledgeOptions, addEditorSubKnowledge, loadSubKnowledgeCatalog, subKnowledgeForDraft,
    matchesQuestionCurriculumFilters, renderSubKnowledgeBadges});

// Formatting operates on one editor snapshot; delayed replies cannot replace
// newer typing or a different question/draft, and never advance the saved state.
async function normalizeEditorFractions(target, button) {
    if (!['editContent', 'editAnswerMarkdown', 'editReview'].includes(target)) return;
    const input = document.getElementById(target);
    if (!input || !button || button.disabled) return;
    const source = input.value;
    if (!source.trim()) {
        showToast('请先输入需要规范分式的内容。', 'info');
        return;
    }
    const session = EditorState.snapshot();
    let edited = false;
    const markEdited = () => { edited = true; };
    input.addEventListener('input', markEdited);
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
        const form = new FormData();
        form.append('text', source);
        const response = await fetch('/api/format/fractions', { method: 'POST', body: form });
        if (!response.ok) throw new Error('fraction formatting failed');
        const result = await response.json();
        if (!EditorState.isCurrent(session)) return;
        if (edited || input.value !== source) {
            showToast('内容已变化，请重新点击“规范分式”。', 'info');
            return;
        }
        if (typeof result.text !== 'string') throw new Error('invalid formatting response');
        if (result.text === source) {
            showToast('未发现可调整的分式；仅处理完整数学环境中的标准分式。', 'info');
            return;
        }
        input.value = result.text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        if (typeof refreshEditorFeedback === 'function') refreshEditorFeedback();
        showToast('分式已规范，请核对预览后保存。', 'success');
    } catch (error) {
        if (EditorState.isCurrent(session)) {
            showToast('分式规范失败，原内容已保留，请稍后重试。', 'error');
        }
    } finally {
        input.removeEventListener('input', markEdited);
        button.disabled = false;
        button.removeAttribute('aria-busy');
    }
}
window.normalizeEditorFractions = normalizeEditorFractions;

// 功能标签展示文案始终取自受控词表（tag_schema.json），这样往词表里新增取值
// （例如"典型题"）时列表徽标会同步出现，无需再改前端代码。
function functionTagLabel(code) {
    const value = String(code || '').trim();
    if (!value) return '';
    const state = (window.MathBankTags && window.MathBankTags.state) || {};
    const dimensions = (state.schema && state.schema.dimensions) || [];
    const dimension = dimensions.find((item) => item && item.key === 'function_tag');
    const matched = ((dimension && dimension.values) || []).find((item) => item && item.code === value);
    return matched ? matched.label : value;
}
window.functionTagLabel = functionTagLabel;

// 列表卡片上的功能标签徽标（易错题 / 创新题 / 典型题）。无功能标签时返回空串。
function functionTagBadge(tagCodes) {
    const codes = (tagCodes && Array.isArray(tagCodes.function)) ? tagCodes.function : [];
    const code = codes.filter(Boolean)[0];
    if (!code) return '';
    return `<span class="text-[9px] font-bold text-indigo-600 bg-indigo-50 border border-indigo-300/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5 cursor-default" title="功能标签"><i class="fa-solid fa-bolt text-[7px] text-indigo-500 mr-0.5"></i><span>${window.MathBankSafe.escapeText(functionTagLabel(code))}</span></span>`;
}
window.functionTagBadge = functionTagBadge;

        function initResizers() {
            const workspace = document.getElementById('bankWorkspaceSection');
            const sidebar = document.getElementById('sidebarSection');
            const preview = document.getElementById('previewSection');
            const resizer1 = document.getElementById('resizer-1');
            if (!workspace || !sidebar || !preview || !resizer1) return;

            const minRatio = Number(resizer1.getAttribute('aria-valuemin')) || 34;
            const maxRatio = Number(resizer1.getAttribute('aria-valuemax')) || 72;
            const defaultRatio = 45;
            let isDragging = false;

            function setBankSplitRatio(value, { notify = false } = {}) {
                const ratio = Math.min(maxRatio, Math.max(minRatio, Number(value) || defaultRatio));
                workspace.style.setProperty('--bank-list-track', `${ratio}fr`);
                workspace.style.setProperty('--bank-detail-track', `${100 - ratio}fr`);
                resizer1.setAttribute('aria-valuenow', String(Math.round(ratio)));
                if (notify) window.dispatchEvent(new Event('resize'));
                return ratio;
            }

            function ratioFromPointer(clientX) {
                const listRect = sidebar.getBoundingClientRect();
                const detailRect = preview.getBoundingClientRect();
                const dividerWidth = resizer1.getBoundingClientRect().width;
                const availableWidth = Math.max(1, detailRect.right - listRect.left - dividerWidth);
                const desiredListWidth = clientX - listRect.left - dividerWidth / 2;
                return desiredListWidth / availableWidth * 100;
            }

            function finishDragging(event) {
                if (!isDragging) return;
                isDragging = false;
                resizer1.classList.remove('is-dragging');
                document.body.style.cursor = '';
                document.body.classList.remove('select-none');
                if (event && resizer1.hasPointerCapture && resizer1.hasPointerCapture(event.pointerId)) {
                    resizer1.releasePointerCapture(event.pointerId);
                }
                window.dispatchEvent(new Event('resize'));
            }

            resizer1.addEventListener('pointerdown', event => {
                if (window.matchMedia('(max-width: 960px)').matches) return;
                event.preventDefault();
                isDragging = true;
                resizer1.classList.add('is-dragging');
                document.body.style.cursor = 'col-resize';
                document.body.classList.add('select-none');
                if (resizer1.setPointerCapture) resizer1.setPointerCapture(event.pointerId);
                setBankSplitRatio(ratioFromPointer(event.clientX));
            });

            resizer1.addEventListener('pointermove', event => {
                if (!isDragging) return;
                event.preventDefault();
                setBankSplitRatio(ratioFromPointer(event.clientX));
            });

            resizer1.addEventListener('pointerup', finishDragging);
            resizer1.addEventListener('pointercancel', finishDragging);

            resizer1.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
                event.preventDefault();
                const currentRatio = Number(resizer1.getAttribute('aria-valuenow')) || defaultRatio;
                const nextRatio = event.key === 'Home'
                    ? defaultRatio
                    : currentRatio + (event.key === 'ArrowLeft' ? -2 : 2);
                setBankSplitRatio(nextRatio, { notify: true });
            });

            resizer1.addEventListener('dblclick', () => {
                setBankSplitRatio(defaultRatio, { notify: true });
            });

            window.setBankSplitRatio = setBankSplitRatio;
        }

        // Copy Original LaTeX content to Clipboard
        function copyPaperContent() {
            const text = document.getElementById('editContent').value;
            if (!text || !text.trim()) {
                showToast('题干内容为空，无法复制！', 'error');
                return;
            }
            navigator.clipboard.writeText(text).then(() => {
                const btn = document.getElementById('copyContentBtn');
                const originalHTML = btn.innerHTML;
                btn.innerHTML = `<i class="fa-solid fa-check text-green-500"></i><span class="text-[9px] font-bold text-green-500">已复制</span>`;
                showToast('题干 LaTeX 代码已成功复制！', 'success');
                setTimeout(() => {
                    btn.innerHTML = originalHTML;
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy: ', err);
                showToast('复制失败，请手动选择复制。', 'error');
            });
        }

        function copyPaperAnalysis() {
            const text = document.getElementById('editAnswerMarkdown').value;
            if (!text || !text.trim()) {
                showToast('解析内容为空，无法复制！', 'error');
                return;
            }
            navigator.clipboard.writeText(text).then(() => {
                const btn = document.getElementById('copyAnalysisBtn');
                const originalHTML = btn.innerHTML;
                btn.innerHTML = `<i class="fa-solid fa-check text-green-500"></i><span class="text-[9px] font-bold text-green-500">已复制</span>`;
                showToast('答案解析 LaTeX 代码已成功复制！', 'success');
                setTimeout(() => {
                    btn.innerHTML = originalHTML;
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy: ', err);
                showToast('复制失败，请手动选择复制。', 'error');
            });
        }

        // Save callbacks may arrive after the user has typed more text. Accepting
        // an explicit request snapshot keeps those later edits dirty instead of
        // accidentally treating the current DOM as the server-confirmed state.
        function backupEditorState(id = null, draftId = null, requestSnapshot = null) {
            const snapshot = requestSnapshot || {
                content: document.getElementById('editContent').value,
                answer_markdown: document.getElementById('editAnswerMarkdown').value,
                review: document.getElementById('editReview').value,
                question_type: document.getElementById('editQType').value,
                difficulty: document.getElementById('editDifficulty').value,
                source: document.getElementById('editSource').value,
                related_question_id: document.getElementById('editRelatedQuestion').value,
                image_paths: JSON.stringify(uploadedImages),
                tikz_code: TikzState.contentAssets[0] ? TikzState.contentAssets[0].tikz_code : '',
                tikz_reference_image_path: TikzState.contentAssets[0]
                    ? (TikzState.contentAssets[0].reference_image_path || '')
                    : '',
                content_tikz_assets: JSON.stringify(TikzState.contentAssets),
                answer_tikz_assets: JSON.stringify(TikzState.answerAssets),
                figure_align: FigureLayoutState.align,
                figure_size: FigureLayoutState.size,
                image_layouts: JSON.parse(JSON.stringify(FigureLayoutState.imageLayouts || {})),
                figure_align_custom: FigureLayoutState.customAlign,
                tags: document.getElementById('editTags') ? document.getElementById('editTags').value : '',
                sub_knowledge_by_version: typeof editorSubKnowledgeVersionSnapshot === 'function' ? editorSubKnowledgeVersionSnapshot() : {}
            };
            originalQuestionState = {
                id: id,
                draftId: draftId,
                content: snapshot.content,
                answer_markdown: snapshot.answer_markdown,
                review: snapshot.review,
                question_type: snapshot.question_type,
                difficulty: snapshot.difficulty,
                source: snapshot.source,
                related_question_id: snapshot.related_question_id || '',
                image_paths: snapshot.image_paths,
                tikz_code: snapshot.tikz_code || '',
                tikz_reference_image_path: snapshot.tikz_reference_image_path || '',
                content_tikz_assets: snapshot.content_tikz_assets || '[]',
                answer_tikz_assets: snapshot.answer_tikz_assets || '[]',
                figure_align: snapshot.figure_align || 'right',
                figure_size: snapshot.figure_size || 'auto',
                image_layouts: snapshot.image_layouts || {},
                figure_align_custom: Boolean(snapshot.figure_align_custom),
                tags: snapshot.tags,
                // 只有「本次真正保存下来的版本」才并进基线；其他版本仍待入库的
                // 细分必须继续算未保存，否则用户保存 A 后会丢掉 B 的待办提醒。
                sub_knowledge_by_version: requestSnapshot && requestSnapshot.version_code
                    ? {...(originalQuestionState?.sub_knowledge_by_version || {}),
                       [requestSnapshot.version_code]: (typeof getEditorSubKnowledge === 'function' ? getEditorSubKnowledge() : [])}
                    : (snapshot.sub_knowledge_by_version || {})
            };
            if (typeof refreshEditorFeedback === 'function') refreshEditorFeedback();
        }
        window.backupEditorState = backupEditorState;

        // Association endpoints save independently of the rest of the editor.
        // Advance only this field so concurrent content/answer edits stay dirty.
        function commitEditorRelatedBaseline(session, relatedQuestionId) {
            if (!originalQuestionState || !EditorState.isCurrent(session)
                    || originalQuestionState.id !== session.questionId) {
                return false;
            }
            originalQuestionState.related_question_id = String(relatedQuestionId || '');
            return true;
        }

        function commitEditorFigureLayoutBaseline(
            questionId,
            figureAlign,
            figureSize,
            figureAlignCustom = true
        ) {
            const normalizedId = Number(questionId);
            if (!originalQuestionState || !window.EditorState
                    || EditorState.questionId !== normalizedId
                    || Number(originalQuestionState.id) !== normalizedId) {
                return false;
            }
            if (!['right', 'bottom_left', 'center', 'bottom_right'].includes(figureAlign)) return false;
            if (!['auto', 'small', 'medium', 'large'].includes(figureSize)) return false;
            originalQuestionState.figure_align = figureAlign;
            originalQuestionState.figure_size = figureSize;
            originalQuestionState.figure_align_custom = Boolean(figureAlignCustom);
            return true;
        }
        window.commitEditorFigureLayoutBaseline = commitEditorFigureLayoutBaseline;

        function reconcileEditorFigureLayout(questionId, confirmed, requested, succeeded) {
            const normalizedId = Number(questionId);
            if (!originalQuestionState || !window.EditorState || !window.FigureLayoutState
                    || EditorState.questionId !== normalizedId
                    || Number(originalQuestionState.id) !== normalizedId
                    || typeof FigureLayoutState.snapshot !== 'function') {
                return false;
            }
            const validAlign = value => ['right', 'bottom_left', 'center', 'bottom_right'].includes(value);
            const validSize = value => ['auto', 'small', 'medium', 'large'].includes(value);
            const confirmedAlign = confirmed && confirmed.figure_align;
            const confirmedSize = confirmed && confirmed.figure_size;
            const confirmedCustom = Boolean(confirmed && confirmed.figure_align_custom);
            const requestedAlign = requested && requested.figure_align;
            const requestedSize = requested && requested.figure_size;
            const requestedCustom = Boolean(requested && requested.figure_align_custom);
            if (!validAlign(confirmedAlign) || !validSize(confirmedSize)
                    || !validAlign(requestedAlign) || !validSize(requestedSize)) {
                return false;
            }

            const current = FigureLayoutState.snapshot();
            const baselineAlign = originalQuestionState.figure_align || 'right';
            const baselineSize = originalQuestionState.figure_size || 'auto';
            const baselineCustom = Boolean(originalQuestionState.figure_align_custom);
            const wasClean = current.figure_align === baselineAlign
                && current.figure_size === baselineSize
                && Boolean(current.figure_align_custom) === baselineCustom;
            const matchesRequested = current.figure_align === requestedAlign
                && current.figure_size === requestedSize
                && Boolean(current.figure_align_custom) === requestedCustom;

            if (succeeded) {
                originalQuestionState.figure_align = confirmedAlign;
                originalQuestionState.figure_size = confirmedSize;
                originalQuestionState.figure_align_custom = confirmedCustom;
                if (wasClean || matchesRequested) {
                    FigureLayoutState.setAlign(confirmedAlign);
                    FigureLayoutState.setSize(confirmedSize);
                    FigureLayoutState.setCustomAlign(confirmedCustom);
                }
            } else if (matchesRequested) {
                FigureLayoutState.setAlign(baselineAlign);
                FigureLayoutState.setSize(baselineSize);
                FigureLayoutState.setCustomAlign(baselineCustom);
            }
            return true;
        }
        window.reconcileEditorFigureLayout = reconcileEditorFigureLayout;

        function editorMatchesBackupSnapshot(snapshot) {
            if (!snapshot) return false;
            const currentContent = document.getElementById('editContent').value;
            const currentAnswer = document.getElementById('editAnswerMarkdown').value;
            const currentReview = document.getElementById('editReview').value;
            const currentType = document.getElementById('editQType').value;
            const currentDifficulty = document.getElementById('editDifficulty').value;
            const currentSource = document.getElementById('editSource').value;
            const currentRelatedQuestionId = document.getElementById('editRelatedQuestion').value;
            const currentImages = JSON.stringify(uploadedImages);
            const currentTikzCode = TikzState.contentAssets[0]
                ? TikzState.contentAssets[0].tikz_code
                : '';
            const currentTikzReferencePath = TikzState.contentAssets[0]
                ? (TikzState.contentAssets[0].reference_image_path || '')
                : '';
            const currentContentTikzAssets = JSON.stringify(TikzState.contentAssets);
            const currentAnswerTikzAssets = JSON.stringify(TikzState.answerAssets);
            const currentFigureAlign = FigureLayoutState.align;
            const currentFigureSize = FigureLayoutState.size;
            const currentFigureAlignCustom = FigureLayoutState.customAlign;
            const currentTags = document.getElementById('editTags') ? document.getElementById('editTags').value : '';

            return currentContent === snapshot.content &&
                   currentAnswer === snapshot.answer_markdown &&
                   currentReview === snapshot.review &&
                   currentType === snapshot.question_type &&
                   currentDifficulty === snapshot.difficulty &&
                   currentSource === snapshot.source &&
                   currentRelatedQuestionId === (snapshot.related_question_id || '') &&
                   (typeof subKnowledgeVersionsSignature !== 'function' || !snapshot.sub_knowledge_by_version
                       || subKnowledgeVersionsSignature(editorSubKnowledgeVersionSnapshot()) === subKnowledgeVersionsSignature(snapshot.sub_knowledge_by_version)) &&
                   currentImages === snapshot.image_paths &&
                   currentTikzCode === (snapshot.tikz_code || '') &&
                   currentTikzReferencePath === (snapshot.tikz_reference_image_path || '') &&
                   currentContentTikzAssets === (snapshot.content_tikz_assets || '[]') &&
                   currentAnswerTikzAssets === (snapshot.answer_tikz_assets || '[]') &&
                   currentFigureAlign === (snapshot.figure_align || 'right') &&
                   JSON.stringify(FigureLayoutState.imageLayouts || {}) === JSON.stringify(snapshot.image_layouts || {}) &&
                   currentFigureSize === (snapshot.figure_size || 'auto') &&
                   currentFigureAlignCustom === Boolean(snapshot.figure_align_custom) &&
                   currentTags === snapshot.tags;
        }
        window.editorMatchesBackupSnapshot = editorMatchesBackupSnapshot;

        // Helper to check if the current question has been modified from its original loaded state
        function isEditorModified() {
            if (!originalQuestionState) return false;
            return !editorMatchesBackupSnapshot(originalQuestionState);
        }
        window.isEditorModified = isEditorModified;

        // Custom Premium Confirmation Modal for Unsaved Changes (3 Options)
        function showUnsavedChangesModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none";
                modalDiv.setAttribute('role', 'dialog');
                modalDiv.setAttribute('aria-modal', 'true');
                modalDiv.setAttribute('aria-labelledby', 'unsavedChangesModalTitle');
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-sm shadow-2xl p-6 space-y-4 transform scale-100 transition-all border border-slate-100">
                        <div class="flex items-center space-x-2 pb-2 border-b">
                            <i class="fa-solid fa-circle-question text-brand-600 text-base animate-pulse"></i>
                            <h3 id="unsavedChangesModalTitle" class="font-bold text-sm text-slate-800">当前编辑内容有未保存的修改</h3>
                        </div>
                        <p class="text-xs text-slate-500 leading-relaxed">
                            您刚才编辑的题目尚未存入正式题库。请选择您希望如何处理这些修改？
                        </p>
                        <div class="flex flex-col space-y-2 pt-2">
                            <button id="saveToBankBtn" type="button" class="w-full px-4 py-2 bg-brand-600/80 hover:bg-brand-600 text-white rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 backdrop-blur-sm border border-brand-500/20 shadow-sm">
                                <i class="fa-solid fa-cloud-arrow-up"></i>
                                <span>存入本地库 (正式题库)</span>
                            </button>
                            <button id="saveToDraftsBtn" type="button" class="w-full px-4 py-2 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 active:scale-[0.98]">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>暂存至草稿箱</span>
                            </button>
                            <button id="discardBtn" type="button" class="w-full px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 active:scale-[0.98]">
                                <i class="fa-solid fa-trash-can"></i>
                                <span>直接离开 (不保存)</span>
                            </button>
                        </div>
                        <div class="flex justify-end pt-2 border-t">
                            <button id="cancelBtn" type="button" class="px-4 py-1.5 border border-slate-300 rounded-xl text-slate-600 hover:bg-slate-50 transition-all text-[10px] font-medium">
                                返回编辑
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modalDiv);

                const finish = (result) => {
                    window.MathBankModal.close(modalDiv);
                    if (modalDiv.isConnected) modalDiv.remove();
                    resolve(result);
                };
                window.MathBankModal.open(modalDiv, { onEscape: () => finish('cancel') });

                document.getElementById('saveToBankBtn').onclick = () => {
                    finish('bank');
                };

                document.getElementById('saveToDraftsBtn').onclick = () => {
                    finish('drafts');
                };

                document.getElementById('discardBtn').onclick = () => {
                    finish('discard');
                };

                document.getElementById('cancelBtn').onclick = () => {
                    finish('cancel');
                };
            });
        }

        // Custom Premium Confirmation Modal for Missing School Phase (Compulsory)
        function showMissingCompulsoryModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none opacity-0 transition-opacity duration-300";
                modalDiv.setAttribute('role', 'dialog');
                modalDiv.setAttribute('aria-modal', 'true');
                modalDiv.setAttribute('aria-labelledby', 'missingClassificationModalTitle');
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4 transform scale-95 transition-all duration-300 border border-slate-100/55">
                        <div class="flex items-center space-x-2.5 pb-2.5 border-b border-slate-100">
                            <div class="w-8 h-8 rounded-xl bg-brand-50 flex items-center justify-center">
                                <i class="fa-solid fa-wand-magic-sparkles text-brand-600 text-sm animate-pulse"></i>
                            </div>
                            <div>
                                <h3 id="missingClassificationModalTitle" class="font-bold text-sm text-slate-800">题目分类信息不完整</h3>
                                <p class="text-[10px] text-slate-400">MATHBANK 教研分类指引</p>
                            </div>
                        </div>
                        <p class="text-xs text-slate-500 leading-relaxed">
                            为了确保题目能够被精准定位和检索，每道题都需要分配<strong>教材章节（人教A版2019 的册/章/节/小节）</strong>。您可以选择：
                        </p>
                        <div class="flex flex-col space-y-2 pt-1">
                            <button id="manualCompulsoryBtn" type="button" class="w-full px-4 py-2.5 bg-slate-50 hover:bg-slate-100 active:scale-[0.99] text-slate-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-2 border border-slate-200/50">
                                <i class="fa-solid fa-pen-to-square text-slate-500"></i>
                                <span>手动选择 / 输入教材定位</span>
                            </button>
                            <button id="autoSaveClassifyBtn" type="button" class="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 active:scale-[0.99] text-white rounded-xl font-bold transition-all text-xs flex items-center justify-center space-x-2 shadow-sm">
                                <i class="fa-solid fa-wand-magic-sparkles"></i>
                                <span>交给系统自动分析并定位</span>
                            </button>
                        </div>
                        <div class="flex justify-end pt-2 border-t border-slate-100">
                            <button id="cancelCompulsoryBtn" type="button" class="px-4 py-2 border border-slate-200 rounded-xl text-slate-500 hover:bg-slate-50 transition-all text-[11px] font-medium active:scale-[0.98]">
                                取消保存
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modalDiv);

                // Add fade-in transition
                setTimeout(() => {
                    modalDiv.classList.remove('opacity-0');
                    modalDiv.querySelector('div').classList.remove('scale-95');
                    modalDiv.querySelector('div').classList.add('scale-100');
                }, 50);

                const closeModal = (result) => {
                    window.MathBankModal.close(modalDiv);
                    modalDiv.classList.add('opacity-0');
                    modalDiv.querySelector('div').classList.remove('scale-100');
                    modalDiv.querySelector('div').classList.add('scale-95');
                    setTimeout(() => {
                        document.body.removeChild(modalDiv);
                        resolve(result);
                    }, 300);
                };
                window.MathBankModal.open(modalDiv, { onEscape: () => closeModal('cancel') });

                document.getElementById('manualCompulsoryBtn').onclick = () => closeModal('manual');
                document.getElementById('autoSaveClassifyBtn').onclick = () => closeModal('ai');
                document.getElementById('cancelCompulsoryBtn').onclick = () => closeModal('cancel');
            });
        }

        // Check if editor has unsaved changes, show modal if needed, then run callback
        async function checkAndSwitch(actionCallback) {
            if (window.isQuestionSaveInFlight && window.isQuestionSaveInFlight()) {
                showToast('题目正在保存，请等待完成后再切换', 'info');
                return;
            }
            if (isEditorModified()) {
                const choice = await showUnsavedChangesModal();
                
                if (choice === 'bank') {
                    // Try to save to SQLite database
                    const saveSuccess = await saveQuestion();
                    if (saveSuccess) {
                        actionCallback();
                    }
                } else if (choice === 'drafts') {
                    // Save to Drafts Box in LocalStorage
                    saveCurrentToDrafts();
                    actionCallback();
                } else if (choice === 'discard') {
                    // Directly leave
                    actionCallback();
                } else {
                    // 'cancel' -> do nothing!
                }
            } else {
                actionCallback();
            }
        }


        function refreshEditorFeedback() {
            const status = document.getElementById('editorSaveStatus');
            const hint = document.getElementById('editorValidationHint');
            if (!status || !hint) return;
            const missing = [];
            const modal = document.getElementById('editorSection');
            [['editContent', '题干'], ['editQType', '题型'], ['editDifficulty', '难度']].forEach(([id, label]) => {
                const input = document.getElementById(id);
                if (!input) return;
                const empty = !input.value.trim();
                if (empty) missing.push(label);
                input.setAttribute('aria-invalid', empty && modal.dataset.validationAttempted === 'true' ? 'true' : 'false');
            });
            // 教材章节在新体系中是多值标签，至少需挂 1 个节点。
            const tagState = window.MathBankTags && window.MathBankTags.state;
            if (!tagState || !Array.isArray(tagState.chapters) || tagState.chapters.length === 0) {
                missing.push('章节');
            }
            status.textContent = typeof isQuestionSaveInFlight === 'function' && isQuestionSaveInFlight()
                ? '正在保存…' : isEditorModified() ? '有未保存的修改' : EditorState.questionId ? '已保存' : '新题目 · 尚未入库';
            hint.textContent = missing.length ? `保存前请补充：${missing.join('、')}。带 * 的项目为必填项。` : '必填信息已齐全，可以保存。';
            hint.classList.toggle('is-complete', missing.length === 0);
        }
        window.refreshEditorFeedback = refreshEditorFeedback;
        function updateBankFilterSummary() {
            const button = document.querySelector('.bank-filter-toggle');
            const clear = document.getElementById('clearBankFiltersBtn');
            if (!button || !clear) return;
            const selected = ['filterType', 'filterDifficulty', 'filterChapterCode', 'filterThought', 'filterStrategy', 'filterFunctionCode',
                'filterSource', 'filterMicroMethod', 'filterTagText']
                .map(id => document.getElementById(id)).filter(element => element && element.value);
            button.textContent = selected.length ? `筛选 (${selected.length})` : '筛选';
            button.setAttribute('data-tooltip', selected.map(element => element.tagName === 'SELECT' ? element.selectedOptions[0]?.textContent : element.value).join(' / ') || '按题型、难度、章节、宏观数学思想、中观解题策略、来源、标签、微观方法等条件筛选');
            clear.hidden = selected.length === 0;
        }
        function clearBankFilters() {
            ['filterType', 'filterDifficulty', 'filterChapterCode', 'filterThought', 'filterStrategy', 'filterFunctionCode',
                'filterSource', 'filterMicroMethod', 'filterTagText'].forEach(id => {
                const element = document.getElementById(id);
                if (element) element.value = '';
            });
            document.getElementById('clearFilterSourceBtn')?.classList.add('hidden');
            currentBankPage = currentDraftPage = 1;
            if (activeSidebarTab === 'bank') loadQuestions(); else loadDrafts();
        }
        window.clearBankFilters = clearBankFilters;
        function toggleBankFilters(button) {
            const fields = document.getElementById('bankFilterFields');
            const open = fields.classList.toggle('is-open');
            button.setAttribute('aria-expanded', String(open));
            document.getElementById('bankWorkspaceSection').classList.toggle('bank-filters-open', open);
        }
        function showBankDetail() {
            if (!window.matchMedia('(max-width: 960px)').matches || (window.PaperStore && window.PaperStore.activeWorkspace !== 'bank')) return;
            document.getElementById('bankWorkspaceSection').classList.add('bank-detail-open');
            const back = document.querySelector('.bank-detail-back');
            if (back) back.focus({preventScroll: true});
        }
        function closeBankDetail() {
            document.getElementById('bankWorkspaceSection').classList.remove('bank-detail-open');
        }
        window.toggleBankFilters = toggleBankFilters;
        window.showBankDetail = showBankDetail;
        window.closeBankDetail = closeBankDetail;
        document.addEventListener('input', event => {
            if (event.target.closest && event.target.closest('#editorSection')) refreshEditorFeedback();
        });
        document.addEventListener('change', event => {
            if (event.target.closest && event.target.closest('#editorSection')) refreshEditorFeedback();
        });

        let questionEditorRestoreFocus = null;

        function switchQuestionEditorPanel(panelId) {
            const validPanels = ['classification', 'content', 'answer', 'review'];
            const targetPanel = validPanels.includes(panelId) ? panelId : 'classification';
            document.getElementById('editorSection').dataset.panel = targetPanel;

            document.querySelectorAll('[data-editor-panel]').forEach(panel => {
                const isActive = panel.dataset.editorPanel === targetPanel;
                panel.classList.toggle('active', isActive);
                panel.classList.toggle('hidden', !isActive);
                panel.setAttribute('aria-hidden', isActive ? 'false' : 'true');
            });

            document.querySelectorAll('[data-editor-panel-target]').forEach(button => {
                const isActive = button.dataset.editorPanelTarget === targetPanel;
                button.classList.toggle('active', isActive);
                if (isActive) {
                    button.setAttribute('aria-current', 'step');
                } else {
                    button.removeAttribute('aria-current');
                }
            });

            const body = document.querySelector('.question-editor-modal-body');
            if (body) body.scrollTop = 0;
        }

        function setQuestionEditorBackgroundInert(isInert) {
            document.querySelectorAll(
                '#appNavigation, #appContentShell > header, #bankWorkspaceSection > :not(#editorSection)'
            ).forEach(element => {
                element.inert = isInert;
                if (isInert) {
                    element.setAttribute('aria-hidden', 'true');
                } else {
                    element.removeAttribute('aria-hidden');
                }
            });
        }

        function setQuestionDetailEditAvailability(isAvailable) {
            const button = document.getElementById('editQuestionFromPreviewBtn');
            if (!button) return;
            button.disabled = !isAvailable;
            button.setAttribute('aria-disabled', isAvailable ? 'false' : 'true');
        }

        function openQuestionEditorModal(panelId = 'content') {
            const modal = document.getElementById('editorSection');
            const editButton = document.getElementById('editQuestionFromPreviewBtn');
            if (!modal) return;
            if (editButton && editButton.disabled && !modal.dataset.allowNewQuestion) {
                showToast('请先从题库中选择一道题目', 'info');
                return;
            }

            questionEditorRestoreFocus = document.activeElement;
            switchQuestionEditorPanel(panelId);
            if (panelId === 'content' && typeof switchContentTab === 'function') {
                switchContentTab('ocr');
            }
            modal.classList.remove('hidden');
            modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('question-editor-open');
            setQuestionEditorBackgroundInert(true);

            requestAnimationFrame(() => {
                const activeStep = modal.querySelector('[data-editor-panel-target].active');
                const title = document.getElementById('editorTitle');
                if (title) { title.tabIndex = -1; title.focus({ preventScroll: true }); }
            });
            delete modal.dataset.allowNewQuestion;
            delete modal.dataset.validationAttempted;
            // 每次打开编辑弹窗都刷新一次来源候选缓存，新录入的来源要立刻可候选。
            if (typeof window.refreshEditorSourceCandidates === 'function') {
                window.refreshEditorSourceCandidates();
            }
            refreshEditorFeedback();
        }

        function finishCloseQuestionEditorModal() {
            const modal = document.getElementById('editorSection');
            if (!modal || modal.classList.contains('hidden')) return;
            modal.classList.add('hidden');
            modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('question-editor-open');
            setQuestionEditorBackgroundInert(false);
            if (questionEditorRestoreFocus && questionEditorRestoreFocus.isConnected) {
                questionEditorRestoreFocus.focus({ preventScroll: true });
            }
            questionEditorRestoreFocus = null;
            // 关闭弹窗时收起仍在浮动的标签记忆 / 历史输入面板，避免其残留在页面上错位。
            if (window.MathBankTagMemory && typeof window.MathBankTagMemory.closePanel === 'function') {
                window.MathBankTagMemory.closePanel();
            }
            if (window.MathBankFilterHistory && typeof window.MathBankFilterHistory.closePanel === 'function') {
                window.MathBankFilterHistory.closePanel();
            }
        }

        function restoreEditorAfterDiscard() {
            if (!originalQuestionState) return;
            if (originalQuestionState.id && typeof selectQuestion === 'function') {
                selectQuestion({ id: originalQuestionState.id }, { silent: true });
                return;
            }
            if (originalQuestionState.draftId) {
                const draft = getLocalStorageDrafts().find(item => item.id === originalQuestionState.draftId);
                if (draft) selectDraft(draft);
                return;
            }
            if (typeof startNewQuestionWithoutPrompt === 'function') {
                startNewQuestionWithoutPrompt();
            }
        }

        function closeQuestionEditorModal() {
            const modal = document.getElementById('editorSection');
            if (!modal || modal.classList.contains('hidden')) return;
            const shouldRestore = isEditorModified();
            const isNewQuestionMode = modal.dataset.newQuestionMode === 'true';
            const returnQuestionId = Number(modal.dataset.returnQuestionId || 0);
            checkAndSwitch(() => {
                finishCloseQuestionEditorModal();
                if (isNewQuestionMode && Number.isSafeInteger(returnQuestionId) && returnQuestionId > 0) {
                    selectQuestion({ id: returnQuestionId }, { silent: true });
                } else if (shouldRestore) {
                    restoreEditorAfterDiscard();
                }
                delete modal.dataset.newQuestionMode;
                delete modal.dataset.returnQuestionId;
            });
        }

        function openNewQuestionEditor() {
            checkAndSwitch(() => {
                const returnQuestionId = Number(EditorState.questionId || 0);
                // The list refresh performed by startNewQuestion must not
                // auto-select the first question again and overwrite the new
                // question draft while its modal is opening.
                window.__preserveNewQuestionEditor = true;
                startNewQuestion();
                const title = document.getElementById('editorTitle');
                if (title) title.textContent = '录入新数学题';
                setQuestionDetailEditAvailability(false);
                const modal = document.getElementById('editorSection');
                if (modal) {
                    modal.dataset.allowNewQuestion = 'true';
                    modal.dataset.newQuestionMode = 'true';
                    if (Number.isSafeInteger(returnQuestionId) && returnQuestionId > 0) {
                        modal.dataset.returnQuestionId = String(returnQuestionId);
                    }
                }
                openQuestionEditorModal();
                setQuestionDetailEditAvailability(false);
            });
        }

        document.addEventListener('keydown', event => {
            const modal = document.getElementById('editorSection');
            if (!modal || modal.classList.contains('hidden')) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                closeQuestionEditorModal();
                return;
            }
            if (event.key !== 'Tab') return;
            const focusable = Array.from(modal.querySelectorAll(
                'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter(element => element.getClientRects().length > 0);
            if (focusable.length === 0) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });

        window.switchQuestionEditorPanel = switchQuestionEditorPanel;
        window.openQuestionEditorModal = openQuestionEditorModal;
        window.closeQuestionEditorModal = closeQuestionEditorModal;
        window.openNewQuestionEditor = openNewQuestionEditor;
        window.setQuestionDetailEditAvailability = setQuestionDetailEditAvailability;

        // 「加入专题」入口（详情工具栏按钮调用）：topic.js 未加载时静默不可用。
        window.addCurrentQuestionToTopic = function () {
            const questionId = parseInt(EditorState.questionId, 10);
            if (!questionId) {
                if (window.showToast) window.showToast('请先从题库中选择一道题目', 'info');
                return;
            }
            if (typeof window.openAddToTopicModal === 'function') {
                window.openAddToTopicModal(questionId);
            } else if (window.showToast) {
                window.showToast('专题工坊模块尚未加载', 'info');
            }
        };

        // ==========================================
        //         LOCALSTORAGE DRAFTS SYSTEM
        // ==========================================

        function getLocalStorageDrafts() {
            try {
                return JSON.parse(localStorage.getItem('mathbank_local_drafts')) || [];
            } catch(e) {
                return [];
            }
        }

        function setLocalStorageDrafts(drafts) {
            localStorage.setItem('mathbank_local_drafts', JSON.stringify(drafts));
        }

        function updateDraftCountBadge() {
            const drafts = getLocalStorageDrafts();
            const badge = document.getElementById('draftCount');
            if (badge) {
                badge.textContent = drafts.length;
            }
        }

        function saveCurrentToDrafts() {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return false;
            }
            if (typeof window.syncEditorImageReferences === 'function') window.syncEditorImageReferences();
            const content = document.getElementById('editContent').value;
            const qtype = document.getElementById('editQType').value;
            const difficulty = document.getElementById('editDifficulty').value;
            const source = document.getElementById('editSource').value;
            const answerMarkdown = document.getElementById('editAnswerMarkdown').value;
            const review = document.getElementById('editReview').value;
            const tags = document.getElementById('editTags') ? document.getElementById('editTags').value.trim() : '';
            const tagSelection = (window.MathBankTags && typeof window.MathBankTags.getSelection === 'function')
                ? window.MathBankTags.getSelection()
                : null;
            
            const draft = {
                id: EditorState.draftId || ('draft-' + Date.now()),
                content: content,
                question_type: qtype,
                difficulty: difficulty,
                source: source,
                answer_markdown: answerMarkdown,
                review: review,
                tags: tags,
                tag_codes: tagSelection ? {
                    chapter: Array.isArray(tagSelection.chapter_codes) ? tagSelection.chapter_codes : [],
                    thought: Array.isArray(tagSelection.thought_codes) ? tagSelection.thought_codes : [],
                    strategy: Array.isArray(tagSelection.strategy_codes) ? tagSelection.strategy_codes : [],
                    micro: Array.isArray(tagSelection.micro_methods) ? tagSelection.micro_methods : [],
                    function: tagSelection.function_code ? [tagSelection.function_code] : []
                } : null,
                image_paths: typeof window.editorAssetReferences === 'function'
                    ? window.editorAssetReferences() : Array.from(new Set([
                    ...uploadedImages,
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : []),
                    ...TikzState.referencePaths()
                ])),
                tikz_code: TikzState.contentAssets[0]
                    ? TikzState.contentAssets[0].tikz_code
                    : '',
                tikz_reference_image_path: TikzState.contentAssets[0]
                    ? (TikzState.contentAssets[0].reference_image_path || '')
                    : '',
                content_tikz_assets: TikzState.contentAssets,
                answer_tikz_assets: TikzState.answerAssets,
                figure_align: FigureLayoutState.align,
                figure_size: FigureLayoutState.size,
                image_layouts: JSON.parse(JSON.stringify(FigureLayoutState.imageLayouts || {})),
                figure_align_custom: FigureLayoutState.customAlign,
                isDraft: true,
                updated_at: new Date().toISOString()
            };
            
            let drafts = getLocalStorageDrafts();
            const index = drafts.findIndex(d => d.id === draft.id);
            if (index > -1) {
                drafts[index] = draft;
            } else {
                drafts.unshift(draft);
            }
            
            setLocalStorageDrafts(drafts);
            EditorState.setDraftId(draft.id);
            
            // Backup the new draft state as the "original state" so the editor is no longer modified
            backupEditorState(EditorState.questionId, draft.id);
            
            updateDraftCountBadge();
            showToast('已暂存至草稿箱！');
            
            // Reload drafts if active
            if (activeSidebarTab === 'drafts') {
                loadDrafts();
            }
        }

        function selectDraft(draft) {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                window.invalidatePendingQuestionDetailLoad();
            }
            if (draft.saved_question_id) {
                EditorState.useQuestion({id: draft.saved_question_id});
                EditorState.setDraftId(draft.id);
            } else EditorState.useDraft(draft);
            
            // Populate form fields
            document.getElementById('editContent').value = draft.content || '';
            setEditorMetadataValue(document.getElementById('editQType'), draft.question_type || 'single_choice');
            setEditorMetadataValue(document.getElementById('editDifficulty'), draft.difficulty || 'medium');
            document.getElementById('editSource').value = draft.source || '';
            document.getElementById('editAnswerMarkdown').value = draft.answer_markdown || '';
            document.getElementById('editReview').value = draft.review || '';
            if (document.getElementById('editTags')) {
                document.getElementById('editTags').value = draft.tags || '';
            }
            
            if (window.MathBankTags && typeof window.MathBankTags.setSelection === 'function') {
                window.MathBankTags.setSelection(draft.tag_codes || null);
            }
            hydrateEditorSubKnowledge(draft, true);
            
            // Load images
            const allDraftImages = Array.isArray(draft.image_paths)
                ? draft.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                : [];
            uploadedAnswerImages = typeof window.collectAnswerImagePaths === 'function'
                ? window.collectAnswerImagePaths(draft.answer_markdown || '')
                : [];
            uploadedImages = window.MathBankImageAssets.withoutReviewOnly(
                allDraftImages, draft.content, draft.answer_markdown, draft.review
            ).filter(path => !uploadedAnswerImages.includes(path));
            window.hydrateTikzState(draft);
            FigureLayoutState.hydrate(draft);
            const hiddenTikzReferencePaths = new Set(TikzState.referencePaths());
            uploadedImages = uploadedImages.filter(path => !hiddenTikzReferencePaths.has(path));
            renderIllustrationBadges();
            if (typeof window.renderAnswerImageBadges === 'function') {
                window.renderAnswerImageBadges();
            }
            if (typeof window.renderAnswerTikzAssets === 'function') {
                window.renderAnswerTikzAssets();
            }
            if (typeof window.renderContentTikzAssets === 'function') {
                window.renderContentTikzAssets();
            }
            
            // Update preview and side panels
            if (typeof window.updateContentPreview === 'function') {
                window.updateContentPreview();
            } else {
                document.getElementById('editContent').dispatchEvent(new Event('input'));
            }
            if (typeof window.updateAnswerPreview === 'function') {
                window.updateAnswerPreview();
            } else {
                document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            }
            if (typeof window.updateReviewPreview === 'function') {
                window.updateReviewPreview();
            } else {
                document.getElementById('editReview').dispatchEvent(new Event('input'));
            }
            if (typeof window.updateReviewPanelPreview === 'function') {
                window.updateReviewPanelPreview();
            }
            renderEditorPaperMeta();
            
            document.getElementById('editorTitle').textContent = `编辑草稿 - 暂存中`;
            
            // Backup draft state
            backupEditorState(EditorState.questionId, draft.id);
            setQuestionDetailEditAvailability(true);
            
            // Active highlighting in sidebar drafts list
            if (activeSidebarTab === 'drafts') {
                highlightActiveDraftCard(draft.id);
            }
        }

        function highlightActiveDraftCard(id) {
            const cards = document.querySelectorAll('#questionsList > div');
            cards.forEach(c => {
                if (c.getAttribute('data-draft-id') === id) {
                    c.className = "p-3.5 mx-1.5 rounded-xl border glass-card bg-white cursor-pointer transition-all duration-200 shadow-md ring-2 ring-emerald-100 border-emerald-500 flex flex-col space-y-2 select-none group relative";
                } else {
                    c.className = "p-3.5 mx-1.5 rounded-xl border glass-card hover:bg-white cursor-pointer transition-all duration-200 shadow-sm flex flex-col space-y-2 select-none group relative border-slate-200";
                }
            });
        }

        function loadDrafts() {
            if (typeof updateBankFilterSummary === 'function') updateBankFilterSummary();
            const qListContainer = document.getElementById('questionsList');
            const q = document.getElementById('searchInput').value.trim().toLowerCase();
            const qtype = document.getElementById('filterType').value;
            const difficulty = document.getElementById('filterDifficulty').value;
            const chapterCode = document.getElementById('filterChapterCode') ? document.getElementById('filterChapterCode').value : '';
            const thought = document.getElementById('filterThought') ? document.getElementById('filterThought').value : '';
            const strategy = document.getElementById('filterStrategy') ? document.getElementById('filterStrategy').value : '';
            const microMethod = document.getElementById('filterMicroMethod') ? document.getElementById('filterMicroMethod').value : '';
            const functionCode = document.getElementById('filterFunctionCode') ? document.getElementById('filterFunctionCode').value : '';
            const source = document.getElementById('filterSource') ? document.getElementById('filterSource').value.trim().toLowerCase() : '';
            const tagText = document.getElementById('filterTagText')
                ? document.getElementById('filterTagText').value.trim().toLowerCase() : '';
            
            let drafts = getLocalStorageDrafts();
            
            // Filter by type
            if (qtype) {
                drafts = drafts.filter(item => item.question_type === qtype);
            }
            
            // Filter by difficulty
            if (difficulty) {
                drafts = drafts.filter(item => item.difficulty === difficulty);
            }
            
            // Filter by chapter code (多值标签，上级节点覆盖其子树)
            if (chapterCode) {
                drafts = drafts.filter(item => {
                    const codes = item.tag_codes && Array.isArray(item.tag_codes.chapter) ? item.tag_codes.chapter : [];
                    return codes.some(code => code === chapterCode || String(code).startsWith(chapterCode + '-'));
                });
            }
            
            // Filter by thought method
            if (thought) {
                drafts = drafts.filter(item => item.tag_codes && Array.isArray(item.tag_codes.thought) && item.tag_codes.thought.includes(thought));
            }

            // Filter by strategy (中观解题策略)
            if (strategy) {
                drafts = drafts.filter(item => item.tag_codes && Array.isArray(item.tag_codes.strategy) && item.tag_codes.strategy.includes(strategy));
            }

            // Filter by micro method (模糊匹配)
            if (microMethod) {
                drafts = drafts.filter(item => item.tag_codes && Array.isArray(item.tag_codes.micro)
                    && item.tag_codes.micro.some(code => code.includes(microMethod)));
            }

            // Filter by function code
            if (functionCode) {
                drafts = drafts.filter(item => item.tag_codes && Array.isArray(item.tag_codes.function) && item.tag_codes.function.includes(functionCode));
            }

            // Filter by source
            if (source) {
                drafts = drafts.filter(item => (item.source || '').toLowerCase().includes(source));
            }

            // Filter by custom tag (填空搜索，模糊匹配自定义标签)
            if (tagText) {
                drafts = drafts.filter(item => {
                    const codes = item.tag_codes && Array.isArray(item.tag_codes.custom) ? item.tag_codes.custom : [];
                    const legacy = String(item.tags || '').split(/[,，]/).map(part => part.trim()).filter(Boolean);
                    return [...codes, ...legacy].some(code => code.toLowerCase().includes(tagText));
                });
            }

            // Search filter for drafts —— 与顶部搜索框同一套覆盖维度
            if (q) {
                drafts = drafts.filter(item => {
                    const codes = item.tag_codes ? Object.values(item.tag_codes).flat() : [];
                    const haystack = [
                        item.content, item.source, item.category_chapter, item.review,
                        item.tags, item.answer_markdown,
                        Array.isArray(codes) ? codes.join(' ') : ''
                    ].map(part => String(part || '').toLowerCase());
                    return haystack.some(part => part.includes(q));
                });
            }
            
            // Sort Drafts by time (updated_at)
            const sortOrder = document.getElementById('filterSort') ? document.getElementById('filterSort').value : 'desc';
            drafts.sort((a, b) => {
                let dateA = a.updated_at ? new Date(a.updated_at).getTime() : 0;
                let dateB = b.updated_at ? new Date(b.updated_at).getTime() : 0;
                
                if (!dateA && a.id && String(a.id).startsWith('draft-')) {
                    const parts = String(a.id).split('-');
                    if (parts.length > 1) {
                        dateA = parseInt(parts[1], 10) || 0;
                    }
                }
                if (!dateB && b.id && String(b.id).startsWith('draft-')) {
                    const parts = String(b.id).split('-');
                    if (parts.length > 1) {
                        dateB = parseInt(parts[1], 10) || 0;
                    }
                }
                
                if (dateA !== dateB) {
                    return sortOrder === 'asc' ? dateA - dateB : dateB - dateA;
                }
                return sortOrder === 'asc' ? String(a.id).localeCompare(String(b.id)) : String(b.id).localeCompare(String(a.id));
            });

            const totalItems = drafts.length;
            const totalPages = Math.ceil(totalItems / PAGE_LIMIT) || 1;
            if (currentDraftPage > totalPages) {
                currentDraftPage = totalPages;
            }
            if (currentDraftPage < 1) {
                currentDraftPage = 1;
            }
            
            qListContainer.innerHTML = '';
            
            if (totalItems === 0) {
                qListContainer.innerHTML = `
                    <div class="ui-state ui-state-empty bank-list-state">
                        <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-box-open"></i></span>
                        <strong class="ui-state-title">草稿箱空空如也</strong>
                        <span class="ui-state-description">编辑题目时保存的草稿会显示在这里。</span>
                    </div>`;
                renderSidebarPagination(0, 1, 'drafts');
                return;
            }
            
            const pageItems = drafts.slice((currentDraftPage - 1) * PAGE_LIMIT, currentDraftPage * PAGE_LIMIT);
            
            pageItems.forEach(item => {
                const difficultyBadge = getDifficultyBadge(item.difficulty);
                const typeText = getTypeText(item.question_type);
                
                const itemCard = document.createElement('div');
                itemCard.setAttribute('data-draft-id', item.id);
                
                const isActive = EditorState.draftId === item.id;
                itemCard.className = `p-3.5 mx-1.5 rounded-xl border glass-card hover:bg-white cursor-pointer transition-all duration-200 shadow-sm flex flex-col space-y-2 select-none group relative ${isActive ? 'border-emerald-500 bg-white ring-2 ring-emerald-100 shadow-md' : 'border-slate-200'}`;
                
                const cleanContent = parseMarkdownWithMath(item.content || '', item.image_layouts || {});
                
                let tagsHtml = '';
                if (item.tags) {
                    const tagList = item.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                    if (tagList.length > 0) {
                        const displayTags = tagList.slice(0, 2);
                        const hiddenCount = tagList.length - 2;
                        
                        displayTags.forEach(tag => {
                            tagsHtml += `<span class="text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-300/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[7px] text-amber-500 mr-0.5"></i><span class="max-w-[80px] truncate">${window.MathBankSafe.escapeText(tag)}</span></span>`;
                        });
                        
                        if (hiddenCount > 0) {
                            const fullTagsHtml = tagList.map(tag => `<span class="inline-flex items-center whitespace-nowrap"><i class="fa-solid fa-tag text-[7px] text-amber-500/80 mr-1"></i>${window.MathBankSafe.escapeText(tag)}</span>`).join('<span class="mx-1.5 text-amber-300/50">|</span>');
                            tagsHtml += `
                            <div class="relative flex items-center" onclick="event.stopPropagation()">
                                <span class="peer text-[9px] font-bold text-amber-600 bg-amber-100 border border-amber-300/60 px-1.5 py-0.5 rounded-full cursor-default flex items-center shadow-sm hover:bg-amber-200 transition-colors">+${hiddenCount}</span>
                                <div class="absolute top-full right-0 mt-1.5 w-max max-w-[220px] bg-amber-50 border border-amber-200/80 text-amber-800 text-[10px] px-2.5 py-1.5 rounded-lg shadow-md opacity-0 pointer-events-none peer-hover:opacity-100 transition-opacity duration-150 z-50 font-medium invisible peer-hover:visible">
                                    <div class="flex flex-wrap items-center leading-relaxed">
                                        ${fullTagsHtml}
                                    </div>
                                </div>
                            </div>`;
                        }
                    }
                }

                itemCard.innerHTML = `
                    <div class="flex items-start justify-between">
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 shrink-0 mt-0.5">草稿 • ${window.MathBankSafe.escapeText(typeText)}</span>
                        <div class="flex items-center gap-1.5 justify-end flex-wrap flex-1 ml-2">
                            ${tagsHtml}
                            ${functionTagBadge(item.tag_codes)}
                            ${difficultyBadge}
                            <!-- Delete Button -->
                            <button type="button" aria-label="删除草稿" class="delete-draft-btn text-slate-400 hover:text-red-500 p-0.5 rounded hover:bg-slate-100 transition-all opacity-0 group-hover:opacity-100" title="删除草稿">
                                <i class="fa-solid fa-trash-can text-[10px]"></i>
                            </button>
                        </div>
                    </div>
                    <div class="text-xs text-slate-700 leading-relaxed font-medium ${cleanContent.includes('choices-grid') ? 'question-choice-preview' : 'line-clamp-2'} card-formula-render">${cleanContent || '[未填题干]'}</div>
                    <div class="flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                        <span class="truncate max-w-[120px] font-semibold text-emerald-600" title="${window.MathBankSafe.escapeAttribute(item.chapter_label || item.category_knowledge || item.category_chapter || '未分类')}"><i class="fa-solid fa-box mr-0.5"></i>${window.MathBankSafe.escapeText(item.chapter_label || item.category_knowledge || item.category_chapter || '未分类')}</span>
                        <span class="font-mono text-slate-400">${window.MathBankSafe.escapeText(item.source ? item.source.substring(0, 12) : '草稿暂存')}</span>
                    </div>
                `;

                const deleteButton = itemCard.querySelector('.delete-draft-btn');
                if (deleteButton) {
                    deleteButton.addEventListener('click', (event) => {
                        event.stopPropagation();
                        deleteDraft(item.id);
                    });
                }
                
                // Render KaTeX inline for this card
                try {
                    renderMathInElement(itemCard.querySelector('.card-formula-render'), {
                        delimiters: [
                            {left: '$$', right: '$$', display: false},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: false}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    console.error('KaTeX sidebar rendering error: ', e);
                }
                
                itemCard.onclick = () => {
                    checkAndSwitch(() => selectDraft(item));
                };
                
                qListContainer.appendChild(itemCard);
                adaptChoicesGridLayout(itemCard);
            });
            
            renderSidebarPagination(totalItems, currentDraftPage, 'drafts');
        }

        function deleteDraft(id) {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (confirm('确认要删除这篇草稿吗？')) {
                let drafts = getLocalStorageDrafts();
                drafts = drafts.filter(d => d.id !== id);
                setLocalStorageDrafts(drafts);
                
                showToast('草稿已删除！');
                updateDraftCountBadge();
                
                if (EditorState.draftId === id) {
                    // Reset current draft state
                    EditorState.clearDraft();
                    startNewQuestionWithoutPrompt();
                }
                
                if (activeSidebarTab === 'drafts') {
                    loadDrafts();
                }
            }
        }

        // 统计大屏：功能标签分布。标签名统一走受控词表（functionTagLabel），
        // 词表新增取值会自动出现；字段缺失或接口异常时显示空态，不抛错。
        function renderStatsFunctionTagCounts(counts) {
            const list = document.getElementById('statsFunctionTagList');
            const emptyHint = document.getElementById('statsFunctionTagEmpty');
            if (!list) return;

            const source = (counts && typeof counts === 'object' && !Array.isArray(counts)) ? counts : {};
            // 词表里的每个取值都要展示（含 0），这样新增的"典型题"在统计口径上
            // 一定可见；后端已按词表全量补 0，这里不再过滤掉 0 值。
            const items = Object.keys(source)
                .map((code) => ({ code: String(code || '').trim(), count: Number(source[code]) || 0 }))
                .filter((item) => item.code)
                .sort((a, b) => b.count - a.count);

            if (items.length === 0) {
                list.innerHTML = '';
                if (emptyHint) emptyHint.classList.remove('hidden');
                return;
            }
            if (emptyHint) emptyHint.classList.add('hidden');

            const escape = (window.MathBankSafe && typeof window.MathBankSafe.escapeText === 'function')
                ? window.MathBankSafe.escapeText
                : (value) => String(value == null ? '' : value);

            list.innerHTML = items.map((item) => {
                const label = (typeof window.functionTagLabel === 'function')
                    ? window.functionTagLabel(item.code)
                    : item.code;
                return '<span class="text-[10px] font-bold text-indigo-700 bg-indigo-50 border border-indigo-200 px-2 py-1 rounded-lg flex items-center space-x-1 cursor-default" title="功能标签">'
                    + '<i class="fa-solid fa-bolt text-[8px] text-indigo-500"></i>'
                    + '<span>' + escape(label) + '</span>'
                    + '<span class="font-mono text-indigo-500">' + escape(String(item.count)) + '</span>'
                    + '</span>';
            }).join('');
        }

        function openStatsModal() {
            const modal = document.getElementById('statsModal');
            const triggerButton = document.getElementById('statsOpenBtn');
            if (triggerButton && triggerButton.disabled) return;
            const triggerButtonContent = triggerButton ? triggerButton.innerHTML : '';
            if (triggerButton) {
                triggerButton.disabled = true;
                triggerButton.setAttribute('aria-busy', 'true');
                triggerButton.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i><span>加载统计</span>';
            }
            
            // 🟢 先拉取并渲染数据，让弹窗内部 DOM 完全静态就绪后再显示弹窗，完美消除毛玻璃背景下的二次重绘闪烁冲突
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        globalStatsData = data;
                        
                        // Render total counters
                        document.getElementById('statsTotalCount').textContent = data.total_count;
                        document.getElementById('statsEasyCount').textContent = data.easy_count;
                        document.getElementById('statsMediumCount').textContent = data.medium_count;
                        document.getElementById('statsHardCount').textContent = data.hard_count;

                        // 功能标签分布（后端可能不返回该字段，内部已容错为空态）
                        renderStatsFunctionTagCounts(data.function_tag_counts);
                        
                        // Populate compulsory stages for stats query
                        populateStatsQueryCompulsory();
                        
                        // Set current local Year and Month
                        const now = new Date();
                        document.getElementById('statsYearSelect').value = now.getFullYear().toString();
                        document.getElementById('statsMonthSelect').value = (now.getMonth() + 1).toString();
                        
                        // Render increments calendar
                        renderStatsCalendar();
                        
                        // Reset query selections
                        document.getElementById('statsQueryCompulsory').value = '';
                        const chapSelect = document.getElementById('statsQueryChapter');
                        chapSelect.innerHTML = '<option value="">-- 先选择学段 --</option>';
                        chapSelect.disabled = true;
                        document.getElementById('statsQueryResultEmpty').classList.remove('hidden');
                        document.getElementById('statsQueryResultData').classList.add('hidden');
                        
                        // 数据和图表完全就绪，再顺滑滑入弹窗并淡化背景
                        document.body.classList.add('modal-active');
                        modal.classList.remove('hidden');
                        window.MathBankModal.open(modal, { onEscape: closeStatsModal });
                        setTimeout(() => {
                            modal.classList.remove('opacity-0');
                            modal.querySelector('div').classList.remove('scale-95');
                            modal.querySelector('div').classList.add('scale-100');
                        }, 50);
                    } else {
                        showToast('获取统计大屏数据失败: ' + data.message, 'error');
                    }
                })
                .catch(err => {
                    showToast('请求统计数据出错: ' + err, 'error');
                })
                .finally(() => {
                    if (!triggerButton) return;
                    triggerButton.disabled = false;
                    triggerButton.removeAttribute('aria-busy');
                    triggerButton.innerHTML = triggerButtonContent;
                });
        }

        function closeStatsModal() {
            const modal = document.getElementById('statsModal');
            window.MathBankModal.close(modal);
            document.body.classList.remove('modal-active');
            
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        function populateStatsQueryCompulsory() {
            const compSelect = document.getElementById('statsQueryCompulsory');
            compSelect.innerHTML = '<option value="">-- 选择学段 --</option>';
            // Full stage catalog from the curriculum tree: zero-count books stay visible.
            const catalog = globalStatsData && Array.isArray(globalStatsData.book_catalog)
                ? globalStatsData.book_catalog
                : null;
            if (catalog) {
                catalog.forEach(book => {
                    compSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(book.book_code)}">${window.MathBankSafe.escapeText(book.book_name)}（${Number(book.count) || 0} 题）</option>`;
                });
                return;
            }
            // Backward fallback for cached payloads without book_catalog.
            const stats = globalStatsData && globalStatsData.chapter_stats;
            if (!Array.isArray(stats)) return;
            const seen = {};
            stats.forEach(item => {
                if (!seen[item.book_code]) {
                    seen[item.book_code] = true;
                    compSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(item.book_code)}">${window.MathBankSafe.escapeText(item.book_name)}</option>`;
                }
            });
        }

        function statsQueryBookEntry(bookVal) {
            const catalog = globalStatsData && Array.isArray(globalStatsData.book_catalog)
                ? globalStatsData.book_catalog
                : null;
            if (!catalog || !bookVal) return null;
            return catalog.find(book => book.book_code === bookVal) || null;
        }

        function renderStatsQueryDistribution(listContainer, entries, total, emptyText) {
            listContainer.innerHTML = '';
            if (!Array.isArray(entries) || entries.length === 0) {
                listContainer.innerHTML = `<div class="text-[10px] text-slate-500 text-center py-4">${emptyText}</div>`;
                return;
            }
            entries.forEach(entry => {
                const pct = total ? Math.round((entry.count / total) * 100) : 0;
                listContainer.innerHTML += `
                    <div class="space-y-1 bg-slate-50/70 dark:bg-slate-800/50 p-2 rounded-lg border border-slate-100 dark:border-slate-700/60">
                        <div class="flex justify-between items-center text-[10px] font-semibold text-slate-700 dark:text-slate-200">
                            <span class="truncate pr-2">${window.MathBankSafe.escapeText(entry.name)}</span>
                            <span class="font-mono text-slate-600 dark:text-slate-400 text-[10px]">${entry.count} 题 (${pct}%)</span>
                        </div>
                        <div class="w-full bg-slate-200 dark:bg-slate-700 h-1.5 rounded-full overflow-hidden">
                            <div class="bg-brand-500 h-1.5 rounded-full" style="width: ${pct}%"></div>
                        </div>
                    </div>
                `;
            });
        }

        function showStatsQueryBookSummary(bookVal) {
            const emptyPanel = document.getElementById('statsQueryResultEmpty');
            const dataPanel = document.getElementById('statsQueryResultData');
            const book = statsQueryBookEntry(bookVal);
            if (!book) {
                emptyPanel.classList.remove('hidden');
                dataPanel.classList.add('hidden');
                return;
            }
            emptyPanel.classList.add('hidden');
            dataPanel.classList.remove('hidden');
            document.getElementById('statsQueryCountLabel').textContent = '本册题目总数:';
            document.getElementById('statsQueryCount').textContent = book.count;
            const listContainer = document.getElementById('statsQueryKnowledgeList');
            const chapters = Array.isArray(book.chapters)
                ? book.chapters.map(ch => ({ name: ch.chapter_name, count: ch.count }))
                : [];
            renderStatsQueryDistribution(
                listContainer,
                chapters,
                book.count,
                book.count > 0 ? '本册题目尚未标注到具体章节' : '本册暂无题目'
            );
        }

        function onStatsQueryCompulsoryChange() {
            const bookVal = document.getElementById('statsQueryCompulsory').value;
            const chapSelect = document.getElementById('statsQueryChapter');

            chapSelect.innerHTML = '<option value="">-- 选择章节（可选） --</option>';
            showStatsQueryBookSummary(bookVal);

            const book = statsQueryBookEntry(bookVal);
            const chapters = book && Array.isArray(book.chapters) ? book.chapters : [];
            if (bookVal && chapters.length) {
                chapSelect.disabled = false;
                chapters.forEach(chapter => {
                    chapSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(chapter.chapter_code)}">${window.MathBankSafe.escapeText(chapter.chapter_name)}（${chapter.count}）</option>`;
                });
            } else {
                chapSelect.disabled = true;
            }
        }

        function onStatsQueryChapterChange() {
            const bookVal = document.getElementById('statsQueryCompulsory').value;
            const chapterVal = document.getElementById('statsQueryChapter').value;

            if (!bookVal) {
                document.getElementById('statsQueryResultEmpty').classList.remove('hidden');
                document.getElementById('statsQueryResultData').classList.add('hidden');
                return;
            }
            if (!chapterVal) {
                // Back to the book-level summary instead of a blank panel.
                showStatsQueryBookSummary(bookVal);
                return;
            }

            const emptyPanel = document.getElementById('statsQueryResultEmpty');
            const dataPanel = document.getElementById('statsQueryResultData');
            emptyPanel.classList.add('hidden');
            dataPanel.classList.remove('hidden');

            const book = statsQueryBookEntry(bookVal);
            const chapterEntry = book && Array.isArray(book.chapters)
                ? book.chapters.find(ch => ch.chapter_code === chapterVal)
                : null;

            // Section breakdown comes from the tag-derived chapter_stats payload;
            // chapters without questions simply show zero.
            const stats = globalStatsData && globalStatsData.chapter_stats;
            const statsEntry = Array.isArray(stats)
                ? stats.find(item => item.chapter_code === chapterVal)
                : null;
            const count = chapterEntry ? chapterEntry.count : (statsEntry ? statsEntry.count : 0);
            document.getElementById('statsQueryCountLabel').textContent = '章节题目总数:';
            document.getElementById('statsQueryCount').textContent = count;

            const listContainer = document.getElementById('statsQueryKnowledgeList');
            listContainer.innerHTML = '<div class="text-[10px] text-slate-400 py-4 text-center"><i class="fa-solid fa-spinner animate-spin mr-1"></i>正在计算小节分布...</div>';

            try {
                const sections = (statsEntry && Array.isArray(statsEntry.sections))
                    ? statsEntry.sections.map(sec => ({ name: sec.name, count: sec.count }))
                    : [];
                renderStatsQueryDistribution(
                    listContainer,
                    sections,
                    count,
                    count > 0 ? '本章暂无小节级细分标签' : '本章暂无题目'
                );
            } catch (err) {
                listContainer.innerHTML = '<div class="text-[10px] text-red-500 py-4 text-center">加载失败</div>';
            }
        }

        function renderStatsCalendar() {
            const year = parseInt(document.getElementById('statsYearSelect').value);
            const month = parseInt(document.getElementById('statsMonthSelect').value);
            const grid = document.getElementById('statsCalendarGrid');
            
            grid.innerHTML = '';
            
            // Get first day of month (0 = Sunday, 6 = Saturday)
            const firstDayIndex = new Date(year, month - 1, 1).getDay();
            // Get total days in month
            const daysInMonth = new Date(year, month, 0).getDate();
            
            // Pre-fill empty days for previous month alignment
            for (let i = 0; i < firstDayIndex; i++) {
                const emptyCell = document.createElement('div');
                emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                grid.appendChild(emptyCell);
            }
            
            // Daily additions data
            const dailyAdds = (globalStatsData && globalStatsData.daily_adds) ? globalStatsData.daily_adds : {};
            
            // Generate cells
            for (let day = 1; day <= daysInMonth; day++) {
                const dayCell = document.createElement('div');
                
                // Format YYYY-MM-DD
                const mStr = String(month).padStart(2, '0');
                const dStr = String(day).padStart(2, '0');
                const dateStr = `${year}-${mStr}-${dStr}`;
                
                const count = dailyAdds[dateStr] || 0;
                
                const isToday = (new Date().getFullYear() === year && new Date().getMonth() + 1 === month && new Date().getDate() === day);
                
                if (count > 0) {
                    dayCell.className = `p-1 bg-rose-50/80 dark:bg-rose-500/15 border border-rose-200/60 dark:border-rose-500/30 hover:bg-rose-100/80 dark:hover:bg-rose-500/25 rounded-lg flex flex-col justify-between items-center transition-all shadow-sm cursor-help select-none ${isToday ? 'ring-2 ring-rose-400 dark:ring-rose-400' : ''}`;
                    dayCell.title = `当天最终录入：${count} 道题目`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-bold text-rose-800 dark:text-rose-200 ${isToday ? 'bg-rose-200 dark:bg-rose-500/30 px-1 py-0.5 rounded-md' : ''}">${day}</span>
                        <span class="text-[10px] font-extrabold text-rose-600 dark:text-rose-400 font-mono animate-[bounce_1.5s_infinite]">+${count}</span>
                    `;
                } else {
                    dayCell.className = `p-1 bg-white dark:bg-slate-900/50 border border-slate-200/40 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/60 rounded-lg flex flex-col justify-start items-center transition-all select-none ${isToday ? 'ring-2 ring-brand-500 border-brand-200 dark:ring-brand-500' : ''}`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-medium text-slate-600 dark:text-slate-300 ${isToday ? 'bg-brand-100 dark:bg-brand-900/60 text-brand-700 dark:text-brand-200 px-1 py-0.5 rounded-md font-bold' : ''}">${day}</span>
                    `;
                }
                
                grid.appendChild(dayCell);
            }
            
            // Fill remaining grid spaces to keep calendar layout perfect
            const totalCellsUsed = firstDayIndex + daysInMonth;
            const remainingCells = 42 - totalCellsUsed;
            if (remainingCells > 0 && remainingCells < 7) {
                const limit = totalCellsUsed <= 35 ? 35 : 42;
                const pad = limit - totalCellsUsed;
                for (let i = 0; i < pad; i++) {
                    const emptyCell = document.createElement('div');
                    emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                    grid.appendChild(emptyCell);
                }
            } else {
                for (let i = 0; i < remainingCells; i++) {
                    const emptyCell = document.createElement('div');
                    emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                    grid.appendChild(emptyCell);
                }
            }
        }

        // Populate the new tag-based filter dropdowns (章节 / 数学思想 / 功能).
        function populateCategoryDropdowns() {
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            
            if (!compSelect || !chapSelect || !knowSelect) return;
            if (!categoryTree || typeof categoryTree !== 'object') {
                console.warn('[Security Shield] 分类数据未准备完毕，跳过编辑区分类级联填充');
                return;
            }
            
            // Backup selection values to prevent losing them during async reloads
            const selectedComp = compSelect.value;
            const selectedChap = chapSelect.value;
            const selectedKnow = knowSelect.value;
            
            // 1. Compulsory
            compSelect.innerHTML = '<option value="">-- 选择学段 --</option>';
            Object.keys(categoryTree).forEach(c => {
                compSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(c)}">${window.MathBankSafe.escapeText(c)}</option>`;
            });
            
            compSelect.onchange = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSelect.innerHTML = '<option value="">-- 先选择章节 --</option>';
                knowSelect.disabled = true;
                
                if (comp && categoryTree[comp]) {
                    chapSelect.disabled = false;
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        chapSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(ch)}">${window.MathBankSafe.escapeText(ch)}</option>`;
                    });
                } else {
                    chapSelect.disabled = true;
                }
            };
            
            chapSelect.onchange = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                knowSelect.innerHTML = '<option value="">-- 选择小节 (可不选，默认整章) --</option>';
                
                if (comp && chap && Array.isArray(categoryTree[comp]?.[chap])) {
                    knowSelect.disabled = false;
                    categoryTree[comp][chap].forEach(k => {
                        knowSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(k)}">${window.MathBankSafe.escapeText(k)}</option>`;
                    });
                } else {
                    knowSelect.disabled = !(comp && chap);
                }
            };

            // Retain historical classifications exactly, including a chapter
            // name stored as knowledge, even when absent from today's directory.
            if (selectedComp) {
                setEditorMetadataValue(compSelect, selectedComp);
                compSelect.onchange();
                if (selectedChap) {
                    setEditorMetadataValue(chapSelect, selectedChap);
                    chapSelect.disabled = false;
                    chapSelect.onchange();
                    if (selectedKnow) setEditorMetadataValue(knowSelect, selectedKnow);
                }
            }
            renderSubKnowledgeOptions('edit');
        }

        // Populate Categories in Filters
        function populateFilterDropdowns() {
            const chapterSelect = document.getElementById('filterChapterCode');
            const thoughtSelect = document.getElementById('filterThought');
            const tagState = (window.MathBankTags && window.MathBankTags.state) || {};
            const safe = window.MathBankSafe;

            if (chapterSelect) {
                const previous = chapterSelect.value;
                let options = '<option value="">所有章节</option>';
                const books = Array.isArray(tagState.tree && tagState.tree.books) ? tagState.tree.books : [];
                books.forEach(book => {
                    let bookOptions = '';
                    (book.chapters || []).forEach(chapter => {
                        bookOptions += `<option value="${safe.escapeAttribute(chapter.code)}">第${chapter.no}章 ${safe.escapeText(chapter.name)}</option>`;
                        (chapter.sections || []).forEach(section => {
                            bookOptions += `<option value="${safe.escapeAttribute(section.code)}">　${safe.escapeText(section.label)} ${safe.escapeText(section.name)}</option>`;
                            (section.subsections || []).forEach(sub => {
                                bookOptions += `<option value="${safe.escapeAttribute(sub.code)}">　　${safe.escapeText(sub.label)} ${safe.escapeText(sub.name)}</option>`;
                            });
                        });
                    });
                    if (bookOptions) {
                        options += `<optgroup label="${safe.escapeText(book.name)}">${bookOptions}</optgroup>`;
                    }
                });
                chapterSelect.innerHTML = options;
                if (previous) chapterSelect.value = previous;
            }

            if (thoughtSelect) {
                const previous = thoughtSelect.value;
                let options = '<option value="">所有思想</option>';
                const schema = tagState.schema;
                const dimension = schema && Array.isArray(schema.dimensions)
                    ? schema.dimensions.find(item => item.key === 'thought_method')
                    : null;
                (dimension && dimension.values || []).forEach(value => {
                    options += `<option value="${safe.escapeAttribute(value.code)}">${safe.escapeText(value.label)}</option>`;
                });
                thoughtSelect.innerHTML = options;
                if (previous) thoughtSelect.value = previous;
            }

            // 功能标签取自同一份受控词表，新增取值（如"典型题"）无需再改 HTML。
            const functionSelect = document.getElementById('filterFunctionCode');
            if (functionSelect) {
                const previous = functionSelect.value;
                let options = '<option value="">全部功能</option>';
                const schema = tagState.schema;
                const dimension = schema && Array.isArray(schema.dimensions)
                    ? schema.dimensions.find(item => item.key === 'function_tag')
                    : null;
                const values = (dimension && dimension.values) || [];
                // 词表尚未加载时保留 HTML 里的静态选项，避免筛选器瞬间变空。
                if (values.length) {
                    values.forEach(value => {
                        options += `<option value="${safe.escapeAttribute(value.code)}">${safe.escapeText(value.label)}</option>`;
                    });
                    functionSelect.innerHTML = options;
                }
                if (previous) functionSelect.value = previous;
            }

            // 解题策略（中观）：与思想方法同一套受控词表，声明顺序即展示顺序。
            const strategySelect = document.getElementById('filterStrategy');
            if (strategySelect) {
                const previous = strategySelect.value;
                let options = '<option value="">所有策略</option>';
                const schema = tagState.schema;
                const dimension = schema && Array.isArray(schema.dimensions)
                    ? schema.dimensions.find(item => item.key === 'solve_strategy')
                    : null;
                const values = (dimension && dimension.values) || [];
                if (values.length) {
                    values.forEach(value => {
                        options += `<option value="${safe.escapeAttribute(value.code)}">${safe.escapeText(value.label)}</option>`;
                    });
                    strategySelect.innerHTML = options;
                }
                if (previous) strategySelect.value = previous;
            }

            // 微观解题方法：库内已用取值（按使用次数降序）。
            if (typeof window.refreshMicroMethodFilterOptions === 'function') {
                window.refreshMicroMethodFilterOptions();
            }
        }

        // 微观方法已改为「填空搜索 + 历史记忆」，仅在 DOM 仍是 <select> 时才回填选项。
        window.refreshMicroMethodFilterOptions = function () {
            const select = document.getElementById('filterMicroMethod');
            if (!select || select.tagName !== 'SELECT') return Promise.resolve(false);
            const previous = select.value;
            return Promise.all([
                fetch('/api/tag-values?dim=micro').then(r => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] })),
                fetch('/api/tag-memory?dim=micro').then(r => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] }))
            ]).then(([inUse, memory]) => {
                const merged = [];
                const seen = new Set();
                (inUse.items || []).forEach(item => {
                    const value = String(item.value || '').trim();
                    if (value && !seen.has(value)) { seen.add(value); merged.push({ value, count: item.count || 0 }); }
                });
                (memory.items || []).forEach(item => {
                    const value = String(item.tag || '').trim();
                    if (value && !seen.has(value)) { seen.add(value); merged.push({ value, count: 0 }); }
                });
                let options = '<option value="">所有微观方法</option>';
                merged.forEach(item => {
                    const suffix = item.count > 0 ? `（${item.count}）` : '';
                    options += `<option value="${window.MathBankSafe.escapeAttribute(item.value)}">${window.MathBankSafe.escapeText(item.value)}${suffix}</option>`;
                });
                select.innerHTML = options;
                if (previous) select.value = previous;
                return true;
            });
        };

        // Load and List Saved Questions
        function loadQuestions(retryCount = 0) {
            if (typeof updateBankFilterSummary === 'function') updateBankFilterSummary();
            const loadSequence = ++bankQuestionsLoadSequence;
            if (bankQuestionsRetryTimer) {
                clearTimeout(bankQuestionsRetryTimer);
                bankQuestionsRetryTimer = null;
            }
            if (bankQuestionsLoadController) {
                bankQuestionsLoadController.abort();
            }
            const requestController = typeof AbortController !== 'undefined'
                ? new AbortController()
                : null;
            bankQuestionsLoadController = requestController;

            const q = document.getElementById('searchInput').value;
            const qtype = document.getElementById('filterType').value;
            const difficulty = document.getElementById('filterDifficulty').value;
            const chapterCode = document.getElementById('filterChapterCode') ? document.getElementById('filterChapterCode').value : '';
            const thought = document.getElementById('filterThought') ? document.getElementById('filterThought').value : '';
            const strategy = document.getElementById('filterStrategy') ? document.getElementById('filterStrategy').value : '';
            const microMethod = document.getElementById('filterMicroMethod') ? document.getElementById('filterMicroMethod').value : '';
            const functionCode = document.getElementById('filterFunctionCode') ? document.getElementById('filterFunctionCode').value : '';
            const source = document.getElementById('filterSource') ? document.getElementById('filterSource').value : '';
            const tagText = document.getElementById('filterTagText') ? document.getElementById('filterTagText').value : '';
            const sortOrder = document.getElementById('filterSort') ? document.getElementById('filterSort').value : 'desc';
            const requestedPage = Math.max(1, currentBankPage);
            
            const params = new URLSearchParams();
            if (q) params.append('q', q);
            if (qtype) params.append('qtype', qtype);
            if (difficulty) params.append('difficulty', difficulty);
            if (chapterCode) params.append('chapter_code', chapterCode);
            if (thought) params.append('thought', thought);
            if (strategy) params.append('strategy', strategy);
            if (microMethod) params.append('micro_method', microMethod);
            if (functionCode) params.append('function_code', functionCode);
            if (source) params.append('source', source);
            if (tagText) params.append('tag', tagText);
            params.append('page', String(requestedPage));
            params.append('page_size', String(PAGE_LIMIT));
            params.append('sort', sortOrder === 'asc' ? 'asc' : 'desc');
            
            const qListContainer = document.getElementById('questionsList');
            if (!qListContainer) return;
            qListContainer.setAttribute('aria-busy', 'true');
            const fetchOptions = requestController ? { signal: requestController.signal } : undefined;
            
            fetch(`/api/questions?${params.toString()}`, fetchOptions)
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`HTTP 状态码异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(payload => {
                    if (loadSequence !== bankQuestionsLoadSequence) return;
                    if (!payload || !Array.isArray(payload.items)) {
                        throw new Error('题库分页响应格式无效');
                    }

                    const questions = payload.items;
                    const parsedTotal = Number(payload.total);
                    const totalItems = Number.isFinite(parsedTotal) && parsedTotal >= 0 ? parsedTotal : 0;
                    const parsedTotalPages = Number(payload.total_pages);
                    const totalPages = Number.isFinite(parsedTotalPages) && parsedTotalPages > 0
                        ? parsedTotalPages
                        : Math.max(1, Math.ceil(totalItems / PAGE_LIMIT));
                    const parsedResponsePage = Number(payload.page);
                    const responsePage = Number.isFinite(parsedResponsePage) && parsedResponsePage > 0
                        ? parsedResponsePage
                        : requestedPage;

                    currentBankPage = Math.min(responsePage, totalPages);
                    qListContainer.innerHTML = '';
                    const shouldAutoSelectFirstQuestion = !window.__preserveNewQuestionEditor &&
                        !EditorState.questionId &&
                        questions.length > 0 && !isEditorModified();
                    
                    if (totalItems === 0) {
                        qListContainer.innerHTML = `
                            <div class="ui-state ui-state-empty bank-list-state">
                                <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-box-open"></i></span>
                                <strong class="ui-state-title">未找到匹配题目</strong>
                                <span class="ui-state-description">请调整搜索词或筛选条件后重试。</span>
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        return;
                    }
                    
                    questions.forEach(item => {
                        // Create card element
                        const difficultyBadge = getDifficultyBadge(item.difficulty);
                        const typeText = getTypeText(item.question_type);
                        
                        const itemCard = document.createElement('div');
                        itemCard.className = `question-card bank-question-card p-3.5 mx-1.5 flex flex-col space-y-2 select-none group relative ${EditorState.questionId === item.id ? 'active' : ''}`;
                        itemCard.dataset.id = item.id;
                        
                        const cleanContent = parseMarkdownWithMath(item.content || '', item.image_layouts || {});
                        
                        let tagsHtml = '';
                        if (item.tags) {
                            const tagList = item.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                            if (tagList.length > 0) {
                                const displayTags = tagList.slice(0, 2);
                                const hiddenCount = tagList.length - 2;
                                
                                displayTags.forEach(tag => {
                                    tagsHtml += `<span class="text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-300/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[7px] text-amber-500 mr-0.5"></i><span class="max-w-[80px] truncate">${window.MathBankSafe.escapeText(tag)}</span></span>`;
                                });
                                
                                if (hiddenCount > 0) {
                                    const fullTagsHtml = tagList.map(tag => `<span class="inline-flex items-center whitespace-nowrap"><i class="fa-solid fa-tag text-[7px] text-amber-500/80 mr-1"></i>${window.MathBankSafe.escapeText(tag)}</span>`).join('<span class="mx-1.5 text-amber-300/50">|</span>');
                                    tagsHtml += `
                                    <div class="relative flex items-center" onclick="event.stopPropagation()">
                                        <span class="peer text-[9px] font-bold text-amber-600 bg-amber-100 border border-amber-300/60 px-1.5 py-0.5 rounded-full cursor-default flex items-center shadow-sm hover:bg-amber-200 transition-colors">+${hiddenCount}</span>
                                        <div class="absolute top-full right-0 mt-1.5 w-max max-w-[220px] bg-amber-50 border border-amber-200/80 text-amber-800 text-[10px] px-2.5 py-1.5 rounded-lg shadow-md opacity-0 pointer-events-none peer-hover:opacity-100 transition-opacity duration-150 z-50 font-medium invisible peer-hover:visible">
                                            <div class="flex flex-wrap items-center leading-relaxed">
                                                ${fullTagsHtml}
                                            </div>
                                        </div>
                                    </div>`;
                                }
                            }
                        }

                        // Relationship badge: driven purely by the relation count
                        // the backend derives from question_relations (single track).
                        let relationCount = 0;
                        if (item.relation_count !== undefined && item.relation_count !== null) {
                            const parsedRelationCount = parseInt(item.relation_count, 10);
                            relationCount = Number.isFinite(parsedRelationCount) && parsedRelationCount > 0
                                ? parsedRelationCount : 0;
                        }
                        const relationBadgeHtml = relationCount > 0
                            ? `<span class="bank-question-relation inline-flex items-center gap-0.5 text-[9px] font-bold text-brand-600 bg-brand-50 border border-brand-200/60 px-1.5 py-0.5 rounded-full cursor-default" onclick="event.stopPropagation()" title="关联题目 ${relationCount} 道"><i class="fa-solid fa-link text-[7px]"></i>关联 ${relationCount}</span>`
                            : '';

                        itemCard.innerHTML = `
                            <div class="bank-question-card-header flex items-start justify-between">
                                <span class="bank-question-type text-[10px] font-bold px-2 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0 mt-0.5">${window.MathBankSafe.escapeText(typeText)}</span>
                                <div class="bank-question-badges flex items-center gap-1.5 justify-end flex-wrap flex-1 ml-2">
                                    <div class="bank-question-tags">${tagsHtml}</div>
                                    ${functionTagBadge(item.tag_codes)}
                                    ${difficultyBadge}
                                    ${relationBadgeHtml}
                                    <!-- Add to Topic Button -->
                                    <button type="button" aria-label="加入专题" onclick="event.stopPropagation(); if (window.openAddToTopicModal) { window.openAddToTopicModal(${item.id}); }" class="bank-add-to-topic-btn text-slate-400 hover:text-brand-600 p-0.5 rounded hover:bg-slate-100 transition-all opacity-0 group-hover:opacity-100" title="加入专题">
                                        <i class="fa-solid fa-folder-plus text-[10px]"></i>
                                    </button>
                                    <span class="text-[10px] font-extrabold px-1.5 py-0.5 rounded bg-brand-50 text-brand-600 shadow-sm">#${window.MathBankSafe.escapeText(item.seq_num)}</span>
                                    <!-- Delete Button -->
                                    <button type="button" aria-label="删除题目" onclick="event.stopPropagation(); deleteQuestion(${item.id})" class="text-slate-400 hover:text-red-500 p-0.5 rounded hover:bg-slate-100 transition-all opacity-0 group-hover:opacity-100" title="删除">
                                        <i class="fa-solid fa-trash-can text-[10px]"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="bank-question-excerpt text-xs text-slate-700 leading-relaxed font-medium ${cleanContent.includes('choices-grid') ? 'question-choice-preview' : 'line-clamp-2'} card-formula-render">${cleanContent || '[空白题干]'}</div>
                            <div class="bank-question-meta flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                                <span class="truncate max-w-[120px] font-semibold" title="${window.MathBankSafe.escapeAttribute(item.chapter_label || item.category_knowledge || item.category_chapter || '未分类')}"><i class="fa-solid fa-folder-open mr-0.5"></i>${window.MathBankSafe.escapeText(item.chapter_label || item.category_knowledge || item.category_chapter || '未分类')}</span>
                                <span class="font-mono text-slate-400">${window.MathBankSafe.escapeText(item.source ? item.source.substring(0, 12) : '本地录入')}</span>
                                <time class="bank-question-time" title="录入于 ${window.MathBankSafe.escapeText(formatChineseDate(item.created_at))}">${window.MathBankSafe.escapeText(String(item.created_at || '').slice(0, 10))}</time>
                            </div>
                        `;
                        
                        // Render KaTeX inline for this card
                        try {
                            renderMathInElement(itemCard.querySelector('.card-formula-render'), {
                                delimiters: [
                                    {left: '$$', right: '$$', display: false},
                                    {left: '$', right: '$', display: false},
                                    {left: '\\(', right: '\\)', display: false},
                                    {left: '\\[', right: '\\]', display: false}
                                ],
                                throwOnError: false
                            });
                        } catch(e) {
                            console.error('KaTeX sidebar rendering error: ', e);
                        }
                        
                        itemCard.onclick = () => {
                            checkAndSwitch(() => selectQuestion(item));
                        };
                        
                        qListContainer.appendChild(itemCard);
                        adaptChoicesGridLayout(itemCard);
                    });

                    if (shouldAutoSelectFirstQuestion) {
                        // Initial list hydration is background work, not a
                        // user selection; avoid showing a success toast on
                        // every page refresh while keeping the first preview.
                        selectQuestion(questions[0], { silent: true });
                    }
                    
                    renderSidebarPagination(totalItems, currentBankPage, 'bank');
                })
                .catch(err => {
                    if ((err && err.name === 'AbortError') || loadSequence !== bankQuestionsLoadSequence) {
                        return;
                    }
                    console.error('加载题库列表发生异常:', err);
                    if (retryCount < 3) {
                        console.warn(`[Auto-Retry] 正在尝试第 ${retryCount + 1} 次自适应重新加载题库数据...`);
                        bankQuestionsRetryTimer = setTimeout(() => {
                            if (loadSequence === bankQuestionsLoadSequence) {
                                loadQuestions(retryCount + 1);
                            }
                        }, 1500);
                    } else {
                        qListContainer.innerHTML = `
                            <div class="ui-state ui-state-error bank-list-state" role="alert">
                                <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-triangle-exclamation"></i></span>
                                <strong class="ui-state-title">获取题库列表失败</strong>
                                <span class="ui-state-description">后台服务正在启动或连接超时</span>
                                <button onclick="loadQuestions()" class="ui-state-action inline-flex items-center space-x-1 cursor-pointer">
                                    <i class="fa-solid fa-arrows-rotate"></i><span>重新加载</span>
                                </button>
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        showToast('系统正在连接或初始化后台，加载题库失败，请稍后刷新重试', 'error');
                    }
                })
                .finally(() => {
                    if (loadSequence !== bankQuestionsLoadSequence) return;
                    if (window.__preserveNewQuestionEditor) {
                        window.__preserveNewQuestionEditor = false;
                    }
                    qListContainer.removeAttribute('aria-busy');
                    if (bankQuestionsLoadController === requestController) {
                        bankQuestionsLoadController = null;
                    }
                });
        }

        // ==========================================
        //       SIDEBAR PAGINATION SYSTEM HELPERS
        // ==========================================
        function renderSidebarPagination(totalItems, currentPage, tabType) {
            const container = document.getElementById('sidebarPagination');
            if (!container) return;
            const summary = document.getElementById('questionResultSummary');
            if (summary) {
                summary.textContent = `共 ${totalItems} 道题`;
            }
            
            if (totalItems === 0) {
                container.innerHTML = '';
                container.style.display = 'none';
                return;
            }
            container.style.display = 'flex';
            
            const totalPages = Math.ceil(totalItems / PAGE_LIMIT) || 1;
            
            if (totalPages <= 1) {
                container.innerHTML = '';
                container.style.display = 'none';
                return;
            }
            container.innerHTML = `
                <div class="sidebar-pagination-controls flex items-center justify-between">
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === 1 ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage - 1}, '${tabType}')">上一页</button>
                    <label class="pagination-page-label">第 <input type="number" aria-label="跳转页码" min="1" max="${totalPages}" value="${currentPage}" class="pagination-jump-input" onkeydown="if(event.key==='Enter') jumpToSidebarPage(this.value, ${totalPages}, '${tabType}')"> / ${totalPages} 页</label>
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === totalPages ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage + 1}, '${tabType}')">下一页</button>
                </div>`;
        }

        function goToSidebarPage(page, tabType) {
            if (tabType === 'bank') {
                currentBankPage = page;
                loadQuestions();
            } else {
                currentDraftPage = page;
                loadDrafts();
            }
            // Scroll questionsList back to top gently
            const qListContainer = document.getElementById('questionsList');
            if (qListContainer) {
                qListContainer.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }
        
        function jumpToSidebarPage(value, maxPage, tabType) {
            let page = parseInt(value, 10);
            if (isNaN(page)) return;
            if (page < 1) page = 1;
            if (page > maxPage) page = maxPage;
            goToSidebarPage(page, tabType);
        }

        // Expose to global scope for inline onclick handlers
        window.renderSidebarPagination = renderSidebarPagination;
        window.goToSidebarPage = goToSidebarPage;
        window.jumpToSidebarPage = jumpToSidebarPage;

        // Format ISO Date string to Chinese local datetime: xxxx年xx月xx日xx时xx分
        function escapeEditorMetaText(value) {
            return window.MathBankSafe.escapeText(value);
        }

        function renderEditorPaperMeta() {
            const badges = document.getElementById('paperBadges');
            const sourceEl = document.getElementById('paperFooterSource');
            const editQType = document.getElementById('editQType');
            const editDifficulty = document.getElementById('editDifficulty');
            const editSource = document.getElementById('editSource');
            const editTags = document.getElementById('editTags');

            if (!badges || !sourceEl || !editQType || !editDifficulty || !editSource) {
                return;
            }

            const seqBadge = EditorState.seqNum != null
                ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-slate-100 text-slate-700">编号：#${escapeEditorMetaText(EditorState.seqNum)}</span>`
                : '<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-slate-200 text-slate-500">编号：新题目</span>';

            const createdAtBadge = EditorState.createdAt
                ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 inline-flex items-center"><i class="fa-regular fa-clock mr-1"></i>${escapeEditorMetaText(String(EditorState.createdAt).slice(0, 10))}</span>`
                : '';

            let paperTagsHtml = '';
            const tagsVal = editTags ? editTags.value.trim() : '';
            if (tagsVal) {
                const tagList = tagsVal.split(/[,，]+/).map(tag => tag.trim()).filter(tag => tag.length > 0);
                tagList.forEach(tag => {
                    paperTagsHtml += `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-amber-50 text-amber-600 border border-amber-300/60 flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[8px] text-amber-500 mr-1"></i>${escapeEditorMetaText(tag)}</span>`;
                });
            }

            const diffColor = (typeof getDifficultyColor === 'function')
                ? getDifficultyColor(editDifficulty.value)
                : 'bg-indigo-50 text-indigo-700';

            badges.innerHTML = `
                ${seqBadge}
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-brand-50 text-brand-700">题型：${escapeEditorMetaText(getTypeText(editQType.value))}</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-md ${diffColor}">难度：${escapeEditorMetaText(getDifficultyText(editDifficulty.value))}</span>
                ${createdAtBadge}
                ${paperTagsHtml}
            `;
            sourceEl.textContent = `来源: ${editSource.value || '本地教研录入'}`;
        }
        window.renderEditorPaperMeta = renderEditorPaperMeta;

        const choicesGridResizeObservers = new WeakMap();

        function getPreferredChoicesColumns(grid) {
            const stored = parseInt(grid.dataset.preferredColumns || '', 10);
            if ([1, 2, 4].includes(stored)) return stored;
            if (grid.classList.contains('grid-cols-1')) return 1;
            if (grid.classList.contains('grid-cols-2')) return 2;
            return 4;
        }

        function choicesGridOverflows(grid) {
            return Array.from(grid.children).some(item => {
                const content = item.querySelector('.choices-content') || item;
                const imageMinWidth = grid.closest('.paper-choice-options-row') ? 100 : 140;
                const imageTooSmall = Array.from(content.querySelectorAll('img')).some(img =>
                    Math.min(img.naturalWidth, imageMinWidth) > content.clientWidth + 1
                );
                return imageTooSmall || content.scrollWidth > content.clientWidth + 1;
            });
        }

        function adaptSingleChoicesGrid(grid) {
            if (!grid || !grid.isConnected || grid.clientWidth <= 0) return;
            // Measure the complete options before allowing prose to wrap. Only
            // an indivisible formula wider than one column needs its own scroll.
            grid.querySelectorAll('.choices-math-scroll').forEach(math => math.classList.remove('choices-math-scroll'));
            grid.dataset.choicesMeasuring = 'true';
            const preferred = getPreferredChoicesColumns(grid);
            const candidates = [4, 2, 1].filter(cols => cols <= preferred);
            let selected = 1;

            for (const columns of candidates) {
                grid.style.setProperty('--choices-columns', String(columns));
                // Force the browser to resolve the candidate track widths before
                // comparing each rendered KaTeX option's real scroll width.
                void grid.offsetWidth;
                if (!choicesGridOverflows(grid)) {
                    selected = columns;
                    break;
                }
            }

            grid.style.setProperty('--choices-columns', String(selected));
            grid.dataset.choiceColumns = String(selected);
            delete grid.dataset.choicesMeasuring;
            grid.querySelectorAll('.choices-content .katex').forEach(math => {
                const content = math.closest('.choices-content');
                if (!math.closest('.katex-display') && math.getBoundingClientRect().width > content.clientWidth + 1) {
                    math.classList.add('choices-math-scroll');
                }
            });
        }

        function adaptChoicesGridLayout(root) {
            if (!root) return;
            const grids = [];
            if (root.matches && root.matches('.choices-grid')) grids.push(root);
            if (root.querySelectorAll) grids.push(...root.querySelectorAll('.choices-grid'));

            grids.forEach(grid => {
                adaptSingleChoicesGrid(grid);
                grid.querySelectorAll('img').forEach(img => {
                    if (!img.complete) {
                        img.addEventListener('load', () => adaptSingleChoicesGrid(grid), { once: true });
                    }
                });
                if (typeof ResizeObserver !== 'undefined' && !choicesGridResizeObservers.has(grid)) {
                    const observer = new ResizeObserver(() => {
                        window.requestAnimationFrame(() => adaptSingleChoicesGrid(grid));
                    });
                    observer.observe(grid);
                    choicesGridResizeObservers.set(grid, observer);
                }
            });
        }
        window.adaptChoicesGridLayout = adaptChoicesGridLayout;

        function setupRealtimePreviews() {
            const editContent = document.getElementById('editContent');
            const editAnswer = document.getElementById('editAnswerMarkdown');

            const updateContentPreview = () => {
                const text = editContent.value;
                const previewContainer = document.getElementById('contentPreview');
                const paperContainer = document.getElementById('paperContent');
                
                // Rebuild after every edit, including undo and pasted Markdown.
                if (typeof window.syncEditorImageReferences === 'function') {
                    window.syncEditorImageReferences();
                    renderIllustrationBadges();
                } else if (uploadedImages.length > 0) {
                    uploadedImages = uploadedImages.filter(path => text.includes(path));
                    renderIllustrationBadges();
                }
                
                if (!text.trim()) {
                    previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将展示实时试卷排版效果。</p>';
                    return;
                }
                
                const preparedHtml = renderQuestionPreviewContent(previewContainer, text, { imageLayouts: FigureLayoutState.imageLayouts });
                renderQuestionPreviewContent(paperContainer, text, { preparedHtml: preparedHtml });
                if (typeof window.applyEditorFigureLayoutPreview === 'function') {
                    window.applyEditorFigureLayoutPreview(previewContainer, text);
                    window.applyEditorFigureLayoutPreview(paperContainer, text);
                }
            };

            const updateAnswerPreview = () => {
                // Automatically synchronize answer image badges on manual or programmatic text changes
                if (typeof syncAnswerImagesFromMarkdown === 'function') {
                    syncAnswerImagesFromMarkdown();
                }
                const text = editAnswer.value;
                const previewContainer = document.getElementById('answerPreview');
                const paperContainer = document.getElementById('paperAnalysisContent');
                
                if (!text.trim()) {
                    previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧输入解析内容，此处将实时展示极其精美的 LaTeX 排版...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic">暂无解析内容。</p>';
                    return;
                }
                
                let html = parseMarkdownWithMath(text);
                previewContainer.innerHTML = html;
                paperContainer.innerHTML = html;
                
                try {
                    renderMathInElement(previewContainer, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                    renderMathInElement(paperContainer, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    console.error(e);
                }
                adaptChoicesGridLayout(previewContainer);
                adaptChoicesGridLayout(paperContainer);
            };
            
            // Attach inputs
            editContent.addEventListener('input', debounce(updateContentPreview, 250));
            editAnswer.addEventListener('input', debounce(updateAnswerPreview, 250));
            
            const editReview = document.getElementById('editReview');
            const updateReviewPreview = () => {
                const text = editReview.value;
                const wrapper = document.getElementById('paperReviewWrapper');
                const content = document.getElementById('paperReviewContent');
                
                if (!text.trim()) {
                    if (wrapper) wrapper.classList.add('hidden');
                    if (content) content.innerHTML = '';
                    return;
                }
                
                if (wrapper) wrapper.classList.remove('hidden');
                let html = parseMarkdownWithMath(text);
                if (content) content.innerHTML = html;
                
                try {
                    if (content) {
                        renderMathInElement(content, {
                            delimiters: [
                                {left: '$$', right: '$$', display: true},
                                {left: '$', right: '$', display: false},
                                {left: '\\(', right: '\\)', display: false},
                                {left: '\\[', right: '\\]', display: true}
                            ],
                            throwOnError: false
                        });
                    }
                } catch(e) {
                    console.error('KaTeX review rendering error: ', e);
                }
                if (content) adaptChoicesGridLayout(content);
            };
            editReview.addEventListener('input', debounce(updateReviewPreview, 250));

            // 第 4 步「评述与关联」面板内的实时排版预览。
            // 与上面的 updateReviewPreview（写入 #paperReviewWrapper / #paperReviewContent，
            // 即右侧题目详情面板）完全独立：这里只写 #reviewPreview，两者互不干扰。
            const updateReviewPanelPreview = () => {
                const container = document.getElementById('reviewPreview');
                if (!container) return;
                const text = editReview ? editReview.value : '';

                if (!text.trim()) {
                    container.innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
                    return;
                }

                container.innerHTML = parseMarkdownWithMath(text);

                try {
                    renderMathInElement(container, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                } catch (e) {
                    console.error('KaTeX review panel rendering error: ', e);
                }
            };
            editReview.addEventListener('input', debounce(updateReviewPanelPreview, 250));
            window.updateReviewPanelPreview = updateReviewPanelPreview;

            // Auto-grow the review textarea (min 120px, capped at 60vh with
            // internal scrolling). This listener is intentionally NOT debounced
            // and lives alongside the debounced preview listener above, whose
            // semantics are left untouched.
            const autoGrowReview = () => {
                if (!editReview) return;
                // Keep the auto-grown floor in sync with the `min-h-[200px]`
                // class on #editReview so the review box matches the height of
                // the 题干 editor (the inline height below would otherwise
                // override the utility class).
                const minHeight = 200;
                const maxHeight = Math.max(minHeight, Math.round(window.innerHeight * 0.6));
                editReview.style.height = 'auto';
                const nextHeight = Math.max(minHeight, Math.min(editReview.scrollHeight, maxHeight));
                editReview.style.height = nextHeight + 'px';
                editReview.style.overflowY = editReview.scrollHeight > maxHeight ? 'auto' : 'hidden';
            };
            editReview.addEventListener('input', autoGrowReview);
            autoGrowReview();
            window.autoGrowReview = autoGrowReview;
            
            // Expose update preview functions to global scope to allow synchronous direct updates when loading questions/drafts
            window.updateContentPreview = updateContentPreview;
            window.updateAnswerPreview = updateAnswerPreview;
            window.updateReviewPreview = updateReviewPreview;
            
            // Keep all editor metadata preview updates on one rendering path.
            const editQType = document.getElementById('editQType');
            const editDifficulty = document.getElementById('editDifficulty');
            const editSource = document.getElementById('editSource');
            const editTags = document.getElementById('editTags');

            // 编辑区「来源」与侧栏来源框同属会被 Chrome 记忆并弹「保存的信息」卡的
            // 自由文本，套用 tag-memory 的同一套防自动填充组合拳。只上防护，
            // 不改变取值与预览渲染。
            if (editSource && window.MathBankTagMemory
                && typeof window.MathBankTagMemory.makeAutofillProof === 'function') {
                window.MathBankTagMemory.makeAutofillProof(editSource);
            }

            // 编辑区「来源」的历史输入 + 题库候选面板。
            // 与侧栏 #filterSource 不同（它自带 /api/sources 下拉，再挂历史面板会
            // 双面板叠压），editSource 此前完全没有候选能力，老师只能手打。
            // 候选是 /api/sources 的会话内缓存：attach 时预取一次，之后每次打开
            // 编辑弹窗 / 保存成功后刷新，保证新写入的来源立刻可候选。
            let editorSourceCandidates = [];
            function refreshEditorSourceCandidates() {
                return fetch('/api/sources')
                    .then((res) => (res.ok ? res.json() : []))
                    .then((data) => {
                        editorSourceCandidates = Array.isArray(data)
                            ? data.map((item) => String(item == null ? '' : item).trim()).filter(Boolean)
                            : [];
                        return editorSourceCandidates;
                    })
                    // 断网 / 后端报错时保留上一次的缓存，绝不把候选清成空。
                    .catch(() => editorSourceCandidates);
            }
            window.refreshEditorSourceCandidates = refreshEditorSourceCandidates;

            if (editSource && window.MathBankFilterHistory
                && typeof window.MathBankFilterHistory.attach === 'function') {
                refreshEditorSourceCandidates();
                window.MathBankFilterHistory.attach(editSource, {
                    historyKey: 'edit_source',
                    getCandidates: () => editorSourceCandidates.slice(),
                    candidateTitle: '题库中的来源'
                });
            }

            editQType.addEventListener('change', renderEditorPaperMeta);
            editDifficulty.addEventListener('change', renderEditorPaperMeta);
            editSource.addEventListener('input', renderEditorPaperMeta);
            if (editTags) editTags.addEventListener('input', renderEditorPaperMeta);
            
            // Initial render of meta badges
            renderEditorPaperMeta();
        }

        function cleanChoiceStemParentheses(text) {
            if (!text) return "";
            text = text.trim();
            text = text.replace(/(?:<br\s*\/?>\s*)+$/i, '').trim();
            const pattern = /(?:[\s\xa0\u3000]*[\(（]\s*\$?\s*(?:\\quad|\\qquad|\\hspace\{.*?\}|[\s\xa0\u3000_])*?\s*\$?\s*[\)）]\s*\$?[\s\xa0\u3000]*)+$/;
            let cleaned = text.replace(pattern, '').replace(/\\paren\b/g, '').trim();
            cleaned = cleaned.replace(/(?:<br\s*\/?>\s*)+$/i, '').trim();

            cleaned = normalizePreviewDollarSigns(cleaned);
            const outsideMath = replaceDelimitedMathForPreview(cleaned, () => '');
            const sanitizedDollars = outsideMath.replace(previewLiteralPattern(), '').replace(/\\\$/g, '');
            const dollarCount = (sanitizedDollars.match(/\$/g) || []).length;
            if (dollarCount % 2 !== 0) {
                cleaned += '$';
            }

            return cleaned;
        }
        window.cleanChoiceStemParentheses = cleanChoiceStemParentheses;

        function transformFillinMacro(clean) {
            if (!clean) return "";
            const saved = [];
            const prefix = previewGuardPrefix(clean, 'F');
            const save = (original, kind = 'literal') => {
                const marker = prefix + saved.length + '\uE003';
                saved.push({marker, original, kind});
                return marker;
            };
            function optionMath(value) {
                if (value === undefined) return {value, safe: true};
                let option = value;
                if (option.includes(prefix)) {
                    const item = saved.find(entry => entry.marker === option.trim());
                    if (!item || item.kind !== 'math') return {value, safe: false};
                    option = item.original;
                }
                let complete = false;
                const normalized = replaceDelimitedMathForPreview(option, (whole, inner) => {
                    if (whole !== option.trim()) return whole;
                    complete = true;
                    return inner;
                });
                if (!complete) {
                    for (let index = 0; index < option.length; index++) {
                        if ((option[index] === '$' || option.startsWith('\\(', index) || option.startsWith('\\[', index))
                                && !isLatexTokenEscaped(option, index)) return {value, safe: false};
                    }
                }
                return {value: normalized, safe: true};
            }
            function replaceMacros(source, insideMath) {
                return source.replace(/\\fillin(?:\[([^\]]*?)\])?(?:\[([^\]]*?)\])?/g, function(match, p1, p2, offset) {
                    if (isLatexTokenEscaped(source, offset)) return match;
                    const first = optionMath(p1), second = optionMath(p2);
                    if (!first.safe || !second.safe) return match;
                    function isLengthStr(str) {
                        return /^\s*\d+(?:\.\d+)?\s*(?:cm|mm|in|pt|pc|em|ex)\s*$/i.test(str || '');
                    }

                    let innerTex = '\\underline{\\hspace{1.5cm}}';
                    if (p1 !== undefined && p2 !== undefined) {
                        const len = isLengthStr(p1) ? p1 : '1.5cm';
                        innerTex = '\\underline{\\hspace{' + len + '}' + second.value + '\\hspace{' + len + '}}';
                    } else if (p1 !== undefined) {
                        if (isLengthStr(p1)) {
                            innerTex = '\\underline{\\hspace{' + p1 + '}}';
                        } else {
                            innerTex = '\\underline{\\quad ' + first.value + ' \\quad}';
                        }
                    }

                    // Complete math keeps its original delimiters. A prose
                    // blank owns separate delimiters, even next to $x$.
                    return insideMath ? innerTex : '\\(' + innerTex + '\\)';
                });
            }
            let source = replacePreviewLiteralSpans(clean, original => save(original));
            source = replaceDelimitedMathForPreview(source, (whole, inner, opening, closing) =>
                save(opening + replaceMacros(inner, true) + closing, 'math'));
            source = replaceMacros(source, false);
            saved.slice().reverse().forEach(({marker, original}) => {
                source = source.split(marker).join(original);
            });
            return source;
        }

        function createMathMLSymbol(symbol, attributes = {}) {
            // A fresh MathML leaf for each occurrence: parent builders may
            // add attributes. Keep semantic text alongside the local SVG.
            return {
                type: 'mo', children: [], classes: [],
                attributes: { lspace: '0em', rspace: '0em', stretchy: 'false', ...attributes },
                setAttribute(key, value) { this.attributes[key] = String(value); },
                getAttribute(key) { return this.attributes[key]; },
                toText() { return symbol; },
                toNode() {
                    const node = document.createElementNS('http://www.w3.org/1998/Math/MathML', 'mo');
                    Object.keys(this.attributes).forEach(key => node.setAttribute(key, this.attributes[key]));
                    node.textContent = symbol;
                    return node;
                },
                toMarkup() {
                    const escape = value => String(value).replace(/[&<>"']/g, char => ({
                        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                    }[char]));
                    const attributes = Object.keys(this.attributes)
                        .map(key => ' ' + key + '="' + escape(this.attributes[key]) + '"').join('');
                    return '<mo' + attributes + '>' + escape(symbol) + '</mo>';
                }
            };
        }

        function registerParallelogramSymbol(renderer) {
            // The bundled KaTeX extension API keeps this symbol in every
            // rendering path, including text, scripts and fractions. Draw a
            // fixed local outline instead of depending on a system font, and
            // leave KaTeX's untrusted-command policy unchanged.
            const { Span, SvgNode, PathNode } = renderer.__domTree;
            renderer.__defineFunction({
                type: 'mathbankParallelogram',
                names: ['\\parallelogram'],
                props: { numArgs: 0, allowedInText: true, allowedInArgument: true },
                handler: ({ parser }) => ({ type: 'mathbankParallelogram', mode: parser.mode }),
                htmlBuilder: (group, options) => {
                    const path = new PathNode('mathbankParallelogram',
                        'M230 0H930L700 600H0Z M266 50L70 550H665L860 50Z');
                    const svg = new SvgNode([path], {
                        width: '0.93em', height: '0.6em', viewBox: '0 0 930 600',
                        preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': 'true'
                    });
                    const span = new Span(['mord', 'mb-parallelogram'], [svg], options, {
                        display: 'inline-block', position: 'relative', width: '0.93em', height: '0.6em'
                    });
                    span.height = 0.6;
                    span.depth = 0;
                    span.width = 0.93;
                    span.maxFontSize = options.sizeMultiplier;
                    return span;
                },
                mathmlBuilder: () => createMathMLSymbol('▱')
            });
            renderer.__defineMacro('▱', '\\parallelogram');
        }
        function registerSchoolMathSymbols(renderer) {
            const { Span, SvgNode, PathNode } = renderer.__domTree;
            // exam-zh-symbols defines these zero-argument upright constants.
            // Register macros in the shared renderer so every preview keeps
            // the original TeX and ordinary variable i/e remain italic.
            ['iu', 'upi'].forEach(name => renderer.__defineMacro('\\' + name, '\\mathrm{i}'));
            ['eu', 'upe'].forEach(name => renderer.__defineMacro('\\' + name, '\\mathrm{e}'));
            renderer.__defineMacro('\\ensuremath', context =>
                context.mode === 'text' ? '\\({#1}\\)' : '{#1}');
            renderer.__defineFunction({
                type: 'mathbankArcAccent', names: ['\\mathbankArcAccent'],
                props: { numArgs: 0, allowedInArgument: true },
                handler: ({ parser }) => ({ type: 'mathbankArcAccent', mode: parser.mode }),
                htmlBuilder: (group, options) => {
                    const path = new PathNode('mathbankArcAccent',
                        'M0 280 Q500 -200 1000 280 L1000 330 Q500 -150 0 330 Z');
                    const svg = new SvgNode([path], {
                        width: '100%', height: '0.33em', viewBox: '0 0 1000 330',
                        preserveAspectRatio: 'none', 'aria-hidden': 'true'
                    });
                    // The relative vlist row produced by overset has the base's
                    // actual width. Keep top/bottom auto for its static baseline;
                    // KaTeX still lays out the base, nested macros and scripts.
                    const span = new Span(['mord', 'mb-arc-accent'], [svg], options, {
                        position: 'absolute', left: '0', width: '100%', height: '0.33em'
                    });
                    span.height = 0.33;
                    span.depth = span.width = 0;
                    span.maxFontSize = options.sizeMultiplier;
                    return span;
                },
                mathmlBuilder: () => createMathMLSymbol('⏜', { stretchy: 'true', accent: 'true' })
            });
            ['wideparen', 'overparen', 'widearc'].forEach(name => {
                renderer.__defineMacro('\\' + name, '\\overset{\\mathbankArcAccent}{#1}');
            });
            renderer.__defineMacro('\\overarc', context => {
                context.consumeSpaces();
                if (context.future().text === '[') {
                    context.popToken();
                    let value = '';
                    while (!['EOF', ']'].includes(context.future().text)) value += context.popToken().text;
                    if (context.future().text !== ']' || value.trim() !== '1') {
                        throw new renderer.ParseError('\\overarc supports the default width only; use \\wideparen{...}');
                    }
                    context.popToken();
                }
                return '\\wideparen';
            });
            renderer.__defineFunction({
                type: 'mathbankPerThousand', names: ['\\perthousand', '\\textperthousand', '\\permil'],
                props: { numArgs: 0, allowedInText: true, allowedInArgument: true },
                handler: ({ parser }) => ({ type: 'mathbankPerThousand', mode: parser.mode }),
                htmlBuilder: (group, options) => {
                    const ring = (x, y) => `M${x} ${y - 110}a110 110 0 1 1 0 220a110 110 0 1 1 0 -220Z`
                        + `M${x} ${y - 70}a70 70 0 1 0 0 140a70 70 0 1 0 0 -140Z`;
                    const path = new PathNode('mathbankPerThousand',
                        ring(140, 140) + ring(530, 555) + ring(810, 555) + 'M570 30H625L245 665H190Z');
                    const svg = new SvgNode([path], {
                        width: '0.95em', height: '0.7em', viewBox: '0 0 950 700',
                        preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': 'true'
                    });
                    const span = new Span(['mord', 'mb-perthousand'], [svg], options, {
                        display: 'inline-block', position: 'relative', width: '0.95em', height: '0.7em'
                    });
                    span.height = 0.7;
                    span.depth = 0;
                    span.width = 0.95;
                    span.maxFontSize = options.sizeMultiplier;
                    return span;
                },
                mathmlBuilder: () => createMathMLSymbol('‰')
            });
            renderer.__defineMacro('‰', '\\perthousand');
            renderer.__defineMacro('\\celsius', '\\ensuremath{{}^\\circ\\mathrm{C}}');
            renderer.__defineMacro('℃', '\\celsius');
            renderer.__defineMacro('\\sfrac', '\\ensuremath{{}^{#1}\\!/\\!{}_{#2}}');
            renderer.__defineMacro('\\ang', context => {
                context.consumeSpaces();
                if (context.future().text === '[') {
                    throw new renderer.ParseError('\\ang optional formatting is not supported; use its default numeric form');
                }
                const input = context.consumeArgs(1)[0].slice().reverse().map(token => token.text).join('');
                const parts = input.split(';').map(part => part.trim());
                if (parts.length > 3 || !parts.some(Boolean)
                    || parts.some(part => part && !/^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(part))) {
                    throw new renderer.ParseError('\\ang expects a number or numeric degrees;minutes;seconds');
                }
                const units = ['\\circ', '\\prime', '\\prime\\prime'];
                return '\\ensuremath{' + parts.map((part, index) => part
                    ? part.replace(',', '.') + '^{' + units[index] + '}' : '').join('') + '}';
            });
        }
        if (typeof katex !== 'undefined') {
            katex.__defineMacro('\\textdollar', '\\text{\\$}');
            registerParallelogramSymbol(katex);
            registerSchoolMathSymbols(katex);
        }

        function transformExamZhParenForPreview(text) {
            if (!text) return "";
            return text.replace(/\\paren\b/g, '<span class="exam-zh-paren-preview" role="img" aria-label="选择题作答括号">（&nbsp;&nbsp;）</span>');
        }

        function normalizeNakedMathForPreview(text) {
            if (!text) return "";
            let source = String(text);

            const placeholders = [];
            function save(original) {
                const marker = `\uE000${placeholders.length}\uE001`;
                placeholders.push({ marker, original });
                return marker;
            }
            function protect(pattern, transform) {
                source = source.replace(pattern, function(match) {
                    return save(typeof transform === 'function' ? transform(match) : match);
                });
            }

            source = replacePreviewLiteralSpans(source, save);

            [
                /```[\s\S]*?```/g,
                /`[^`\n]*`/g,
                /\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}/g,
                /!\[[^\]\n]*\]\([^\n)]*\)/g,
                /\]\([^\n)]*\)/g,
                /<mathbank-math\b[^>]*>[\s\S]*?<\/mathbank-math\s*>/g,
                /\[\[MBM_[A-Za-z0-9_:-]+\]\]/g,
                /\[ILLUSTRATION_BOX:\s*[^\]\n]*\]/gi,
                // Cells already contain these tokens when table rendering
                // reaches this helper. They must survive until final restore.
                /@@MATH_PLACEHOLDER_\d+@@/g
            ].forEach(function(pattern) { protect(pattern); });

            source = normalizePreviewDollarSigns(source);
            source = replaceDelimitedMathForPreview(source, save);
            source = replaceLatexEnvironmentsForPreview(source, function(environment, name, body) {
                if (/^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)$/.test(name)) {
                    return save(environment);
                }
                if (/^(equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|displaymath)$/.test(name)) {
                    // KaTeX has no multline environment. Preserve every row
                    // in a centered gathered preview; stored/exported TeX is unchanged.
                    const innerEnvironment = /^alignat/.test(name) ? 'alignedat'
                        : /^align/.test(name) ? 'aligned'
                        : /^(gather|multline)/.test(name) ? 'gathered' : '';
                    const preview = innerEnvironment
                        ? '\\begin{' + innerEnvironment + '}' + body + '\\end{' + innerEnvironment + '}'
                        : body;
                    return save('$$' + preview + '$$');
                }
                return save('$' + (name === 'math' ? body : environment) + '$');
            });
            // Save dollar math and native math environments before URLs, so a
            // URL inside either cannot conceal its closing delimiter/environment.
            protect(/[hH][tT][tT][pP][sS]?:\/\/[^\s<>"']+/g);

            // Bold prose is unpacked after normalization. Normalize geometry
            // within that text now, while keeping the outer textbf protected.
            protect(/\\textbf\{[^{}\n]*\}/g, function(match) {
                if (!/[▱‰℃]|\\(?:parallelogram|perthousand|textperthousand|permil|celsius)(?![A-Za-z])/.test(match)) return match;
                return '\\textbf{' + normalizeNakedMathForPreview(match.slice(8, -1)) + '}';
            });
            // Semicolons in siunitx degree/minute/second input belong to one
            // formula, rather than separating prose-level math candidates.
            protect(/\\ang\s*\{[+\-\d.,;\s]*\}/g, match => '\\(' + match + '\\)');

            [
                /\\(?:begin|end)\{[^}\n]+\}/g,
                /\\item\b/g,
                /\\fillin\b/g,
                /\\paren\b/g,
                /\\includegraphics(?:\s*\[[^\]\n]*\])?\s*\{[^}\n]+\}/g,
                /<\/?[A-Za-z][^>\n]*>/g
            ].forEach(function(pattern) { protect(pattern); });

            source = source.replace(/[A-Za-z0-9▱‰℃\\{}_^+\-*/=<>|(),.:\[\]\t ]+/g, function(raw) {
                const core = raw.trim();
                if (!core) return raw;
                const nonMathCommands = new Set([
                    'begin', 'bottomrule', 'centering', 'cline', 'end', 'fillin',
                    'hline', 'includegraphics', 'item', 'midrule', 'multicolumn',
                    'multirow', 'paren', 'renewcommand', 'textbf', 'toprule'
                ]);
                const commands = core.match(/\\[A-Za-z]+/g) || [];
                const hasMathCommand = commands.some(command => !nonMathCommands.has(command.slice(1).toLowerCase()));
                const hasScript = /[A-Za-z0-9})\]]\s*[_^](?:\s*\{|\s*[A-Za-z0-9\\])/.test(core);
                const hasRelation = /[A-Za-z0-9})\]]\s*(?:=|<|>)\s*(?:[A-Za-z0-9({\[\\+\-])/.test(core);
                const hasFunction = /\b[A-Za-z]\s*\([^)]*[A-Za-z0-9_+\-,\\][^)]*\)/.test(core);
                const hasCoordinate = /\(\s*[+\-]?(?:\d+(?:\.\d+)?|[A-Za-z])\s*,[^)]*\)/.test(core);
                if (!hasMathCommand && !hasScript && !hasRelation && !hasFunction && !hasCoordinate) {
                    // A pasted symbol alone must not turn surrounding English
                    // prose into math or consume adjacent dollar delimiters.
                    return raw.replace(/[▱‰℃]/g, symbol => '\\(' + symbol + '\\)');
                }
                const leading = raw.slice(0, raw.length - raw.trimStart().length);
                const trailing = raw.slice(raw.trimEnd().length);
                return leading + '$' + core + '$' + trailing;
            });

            placeholders.slice().reverse().forEach(function(entry) {
                source = source.replace(entry.marker, function() { return entry.original; });
            });
            return source;
        }

        function previewLiteralPattern() {
            // Bare http(s) URLs and markdown link targets (`](...)`) are no
            // longer captured here: they are routed through the link channel
            // (see the markdown-lite block in preprocessFormulaForKaTeX) so
            // they render as clickable anchors instead of literal <code>.
            // Image markdown (`![alt](src)`) keeps its literal capture so the
            // existing <img> conversion still owns it.
            return /```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|(`+)[^\n]*?\1(?!`)|\\begin\{(tikzpicture|verbatim\*?|Verbatim\*?|lstlisting|minted|comment)\}[\s\S]*?(?:\\end\{\2\}|$)|\\(?:verb|Verb|lstinline)\*?(?:\[[^\]\n]*\])?([^\w\s{])[^\n]*?(?:\3|(?=\r?\n)|$)|\\mintinline(?:\[[^\]\n]*\])?\{[^{}\n]*\}([^\w\s{])[^\n]*?(?:\4|(?=\r?\n)|$)|\\(?:detokenize|url|path|lstinline\*?(?:\[[^\]\n]*\])?|mintinline(?:\[[^\]\n]*\])?\{[^{}\n]*\})\s*\{|<(?:[cC][oO][dD][eE]|[pP][rR][eE])\b[^>]*>[\s\S]*?(?:<\/(?:[cC][oO][dD][eE]|[pP][rR][eE])\s*>|$)|!\[[^\]\n]*\]\([^\n)]*\)|<mathbank-math\b[^>]*>[\s\S]*?<\/mathbank-math\s*>|<\/?[A-Za-z][^>\n]*>|\[\[MBM_[A-Za-z0-9_:-]+\]\]/g;
        }

        function previewLiteralEnd(text, match) {
            let end = match.index + match[0].length;
            const tail = text.slice(match.index);
            const opening = /^(`{3,}|~{3,})([^\r\n]*)/.exec(tail);
            const lineStart = text.lastIndexOf('\n', match.index - 1) + 1;
            if (opening && /^[ \t]{0,3}$/.test(text.slice(lineStart, match.index))
                    && !(opening[1][0] === '`' && opening[2].includes('`'))) {
                const newline = tail.indexOf('\n');
                if (newline < 0) return text.length;
                const bodyStart = match.index + newline + 1;
                const closing = new RegExp('^[ \\t]{0,3}' + opening[1][0]
                    + '{' + opening[1].length + ',}[ \\t]*\\r?$', 'm').exec(text.slice(bodyStart));
                return closing ? bodyStart + closing.index + closing[0].length : text.length;
            }
            // Inline code may use three or more ticks; the broad fence token
            // must not stop at a shorter run inside that literal payload.
            const ticks = /^`+/.exec(tail);
            if (ticks) {
                const closing = new RegExp('`+', 'g');
                closing.lastIndex = match.index + ticks[0].length;
                let next;
                while ((next = closing.exec(text))) {
                    if (next[0].length === ticks[0].length) return closing.lastIndex;
                }
            }
            if (!match[0].endsWith('{') || !/^\\(?:detokenize|url|path|lstinline|mintinline)\b/.test(match[0])) return end;
            let depth = 1;
            while (end < text.length && depth > 0) {
                if (!isLatexTokenEscaped(text, end)) depth += (text[end] === '{') - (text[end] === '}');
                end++;
            }
            return end;
        }

        function previewGuardPrefix(text, kind) {
            let prefix = '\uE002' + kind;
            while (text.includes(prefix)) prefix += kind;
            return prefix;
        }

        function replacePreviewLiteralSpans(text, replace) {
            // The existing scanner gives an earlier math opener precedence.
            // Mask complete math first, then visit only actual literal spans.
            const guards = [];
            const prefix = previewGuardPrefix(text, 'G');
            const saveMath = whole => {
                const marker = prefix + guards.length + '\uE003';
                guards.push({marker, whole});
                return marker;
            };
            let source = replaceDelimitedMathForPreview(text, saveMath);
            source = replaceLatexEnvironmentsForPreview(source, (environment, name) =>
                /^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)$/.test(name) ? environment : saveMath(environment));
            const pattern = previewLiteralPattern();
            const parts = [];
            let copied = 0, match;
            while ((match = pattern.exec(source))) {
                const end = previewLiteralEnd(source, match);
                parts.push(source.slice(copied, match.index), replace(source.slice(match.index, end)));
                copied = pattern.lastIndex = end;
            }
            parts.push(source.slice(copied));
            source = parts.join('');
            guards.slice().reverse().forEach(({marker, whole}) => {
                source = source.split(marker).join(whole);
            });
            return source;
        }

        function normalizePreviewDollarSigns(text) {
            const literal = '\\(\\text{\\$}\\)';
            const tokens = new RegExp(previewLiteralPattern().source + '|'
                + /\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\\textdollar\b(?:\{\})?|\$/.source, 'g');
            let result = '', copied = 0, match;
            while ((match = tokens.exec(text))) {
                const token = match[0], start = match.index;
                if (isLatexTokenEscaped(text, start)) continue;
                const literalEnd = previewLiteralEnd(text, match);
                if (literalEnd > tokens.lastIndex) tokens.lastIndex = literalEnd;
                if (token.startsWith('\\textdollar')) {
                    result += text.slice(copied, start) + literal;
                    copied = tokens.lastIndex;
                } else if (token === '$') {
                    let end = text.indexOf('$', start + 1);
                    while (end >= 0 && isLatexTokenEscaped(text, end)) end = text.indexOf('$', end + 1);
                    const tail = text.slice(start + 1);
                    // A complete numerical product such as $2 xy+3$ is math,
                    // even though its first variable looks like a prose word.
                    // Money-list connectors still identify $5 and $10 as prose.
                    const paired = end >= 0
                        ? /^\d[\d,]*(?:\.\d+)?[ \t]+([A-Za-z][A-Za-z0-9]*)([\s\S]*)$/.exec(text.slice(start + 1, end))
                        : null;
                    const remainder = paired ? paired[2].trim() : '';
                    const mathContinuation = remainder
                        && /^[+\-*/^_=<>\\([{]|^[A-Za-z0-9](?:\b|[_^])/.test(remainder)
                        && !/[.!?;:][ \t\r\n]+[A-Za-z]{2,}\b/.test(remainder);
                    const productMath = paired
                        && (mathContinuation || (!remainder
                            && !/^(?:and|or|to|for|at|each|per|costs?|price|total|dollars?|tax)$/i.test(paired[1])));
                    // Prose amounts are currency; $5$, $2+3$ and multiline
                    // formulas retain their original mathematical meaning.
                    const currency = /^\d[\d,]*(?:\.\d+)?(?:[.,!?;:]?\s+[A-Za-z]{2,}\b|[.,!?;:][ \t]*\r?\n|[.,!?;:]?(?:\s*$))/.test(tail);
                    const isolated = (start === 0 || text[start - 1] === '\n')
                        && /^[ \t]*(?:\n[ \t]*\n|\n[ \t]*\\begin\{choices\}|$)/.test(tail);
                    const amountList = /^\d[\d,]*(?:\.\d+)?[.,;:]?[ \t]+\$\d/.test(tail);
                    if ((!productMath && (currency || amountList)) || isolated || end < 0) {
                        result += text.slice(copied, start) + literal;
                        copied = start + 1;
                    } else {
                        tokens.lastIndex = end + 1;
                    }
                }
            }
            return result + text.slice(copied);
        }

        function isLatexTokenEscaped(text, index) {
            let cursor = index - 1;
            while (cursor >= 0 && text[cursor] === '\\') cursor--;
            return (index - cursor - 1) % 2 === 1;
        }

        function replaceDelimitedMathForPreview(text, replace) {
            const parts = [];
            // Paths and literal examples cannot open a math span that ends in
            // a later real formula. A math opener encountered first still owns
            // its complete contents, including URL-looking text inside math.
            const literalPattern = previewLiteralPattern();
            let literal = literalPattern.exec(text);
            let cursor = 0;
            let copied = 0;
            while (cursor < text.length) {
                if (literal && literal.index < cursor) {
                    literalPattern.lastIndex = cursor;
                    literal = literalPattern.exec(text);
                }
                if (literal && literal.index === cursor) {
                    cursor = previewLiteralEnd(text, literal);
                    literalPattern.lastIndex = cursor;
                    literal = literalPattern.exec(text);
                    continue;
                }
                const opening = ['$$', '$', '\\(', '\\['].find(token => text.startsWith(token, cursor));
                if (!opening || isLatexTokenEscaped(text, cursor)) {
                    cursor++;
                    continue;
                }
                const closing = opening === '\\(' ? '\\)' : opening === '\\[' ? '\\]' : opening;
                let end = text.indexOf(closing, cursor + opening.length);
                while (end >= 0 && isLatexTokenEscaped(text, end)) {
                    end = text.indexOf(closing, end + closing.length);
                }
                if (end < 0) {
                    cursor += opening.length;
                    continue;
                }
                const finish = end + closing.length;
                parts.push(text.slice(copied, cursor), replace(
                    text.slice(cursor, finish), text.slice(cursor + opening.length, end), opening, closing
                ));
                cursor = copied = finish;
            }
            parts.push(text.slice(copied));
            return parts.join('');
        }

        function replaceLatexEnvironmentsForPreview(text, replace) {
            const recognized = /^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr|equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|displaymath|math|cases|aligned|alignedat|gathered|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|smallmatrix|array|split)$/;
            const tokenPattern = /\\(begin|end)\{([^{}\n]+)\}/g;
            const literalPattern = previewLiteralPattern();
            let literal = literalPattern.exec(text);
            const parts = [];
            let copied = 0;
            let match;
            while ((match = tokenPattern.exec(text))) {
                while (literal && literal.index <= match.index) {
                    const end = previewLiteralEnd(text, literal);
                    if (end > match.index) {
                        tokenPattern.lastIndex = end;
                        break;
                    }
                    literalPattern.lastIndex = end;
                    literal = literalPattern.exec(text);
                }
                if (literal && literal.index <= match.index && previewLiteralEnd(text, literal) > match.index) continue;
                const name = match[2];
                if (match[1] !== 'begin' || !recognized.test(name) || isLatexTokenEscaped(text, match.index)) continue;
                const bodyStart = tokenPattern.lastIndex;
                const nestedPattern = /\\(begin|end)\{([^{}\n]+)\}/g;
                nestedPattern.lastIndex = bodyStart;
                const stack = [name];
                let nested;
                while ((nested = nestedPattern.exec(text))) {
                    if (isLatexTokenEscaped(text, nested.index)) continue;
                    if (nested[1] === 'begin') stack.push(nested[2]);
                    else if (nested[2] !== stack[stack.length - 1]) break;
                    else stack.pop();
                    if (stack.length === 0) {
                        const end = nestedPattern.lastIndex;
                        parts.push(text.slice(copied, match.index), replace(
                            text.slice(match.index, end), name, text.slice(bodyStart, nested.index)
                        ));
                        copied = tokenPattern.lastIndex = end;
                        break;
                    }
                }
            }
            parts.push(text.slice(copied));
            return parts.join('');
        }
        window.normalizeNakedMathForPreview = normalizeNakedMathForPreview;

        function preprocessFormulaForKaTeX(text, imageLayouts = {}) {
            if (!text) return "";

            const literalEntries = [];
            const literalPrefix = previewGuardPrefix(text, 'L');
            const protectedText = replacePreviewLiteralSpans(text, original => {
                // Images retain the existing safe conversion and asset path.
                // Other raw HTML continues through the existing sanitizer.
                if (original.startsWith('![') || (original.startsWith('<')
                    && !/^<(?:code|pre)\b/i.test(original))) return original;
                const marker = literalPrefix + literalEntries.length + '\uE003';
                const htmlCode = /^<(?:code|pre)\b/i.test(original);
                const visible = htmlCode ? original.replace(/^<(?:code|pre)\b[^>]*>/i, '')
                    .replace(/<\/(?:code|pre)\s*>$/i, '') : original;
                literalEntries.push({marker, visible});
                return marker;
            });
            
            // Clean up any historical \vphantom{...} or \strut from underline text to prevent KaTeX rendering artifact letters
            let clean = protectedText.replace(/\\vphantom\s*\{\s*[^}]*?\}/g, '')
                            .replace(/\\strut\b/g, '');

            clean = normalizeNakedMathForPreview(clean);

            // Move terminal blank punctuation only within a complete math
            // span. The closing $ of $x$ cannot open a prose \fillin span.
            clean = replaceDelimitedMathForPreview(clean, (whole, inner, opening, closing) => {
                const punctuation = /^([\s\S]*?\\fillin(?:\[[^\]]*?\])?(?:\[[^\]]*?\])?)\s*([。，,；;！？!?\.]+)\s*$/.exec(inner);
                if (!punctuation || isLatexTokenEscaped(inner, punctuation[1].lastIndexOf('\\fillin'))) return whole;
                return opening + punctuation[1] + closing + punctuation[2];
            });

            // Transform exam-zh \fillin macro into KaTeX compatible \underline with math environment awareness
            clean = transformFillinMacro(clean);

            // Safely escape raw < and > inside math environments to \lt and \gt without lookbehind regex for maximum browser compatibility
            clean = replaceDelimitedMathForPreview(clean, function(match, inner, opening, closing) {
                let safeInner = inner.replace(/\\</g, '@@ESCAPED_LESS@@')
                                     .replace(/</g, '\\lt ')
                                     .replace(/@@ESCAPED_LESS@@/g, '\\<')
                                     .replace(/\\>/g, '@@ESCAPED_GREAT@@')
                                     .replace(/>/g, '\\gt ')
                                     .replace(/@@ESCAPED_GREAT@@/g, '\\>');
                return opening + safeInner + closing;
            });

            // Clean up illegal nesting like \underline{\quad $\mathbf{14}$ \quad} in KaTeX
            clean = clean.replace(/(\\underline\s*\{[^}]*?)\$([^$]+?)\$([^}]*?\})/g, function(match, p1, p2, p3) {
                return '$' + p1 + p2 + p3 + '$';
            });
            
            // Protect every complete math shell before wrapping a bare blank;
            // dollar counting cannot identify \( ... \) or an adjacent $x$.
            const underlineMath = [];
            const underlinePrefix = previewGuardPrefix(clean, 'U');
            clean = replaceDelimitedMathForPreview(clean, whole => {
                const marker = underlinePrefix + underlineMath.length + '\uE003';
                underlineMath.push({marker, whole});
                return marker;
            });
            clean = clean.replace(/\\underline\s*\{\s*\\hspace\s*\{([^}]+?)\}\s*\}/g, match => '\\(' + match + '\\)');
            underlineMath.slice().reverse().forEach(({marker, whole}) => {
                clean = clean.split(marker).join(whole);
            });
            
            // Protect math blocks to avoid replacing spacing commands inside math environments
            const placeholders = [];
            let placeholderCounter = 0;
            
            function savePlaceholder(match) {
                const placeholder = `@@MATH_PLACEHOLDER_${placeholderCounter++}@@`;
                placeholders.push({ placeholder, original: match });
                return placeholder;
            }
            
            let tempText = clean;
            tempText = replaceDelimitedMathForPreview(tempText, savePlaceholder);
            
            // Strip HTML tags from non-math parts
            tempText = tempText.replace(/<[^>]*>/g, '');

            // exam-zh defines \paren outside math mode, while KaTeX does not.
            // Convert only the protected-and-sanitized text portion into the
            // same empty answer parentheses used by the A4 paper preview.
            tempText = transformExamZhParenForPreview(tempText);
            
            // Process LaTeX lists & environments outside math blocks
            tempText = tempText.replace(/\\\\\s*\\begin\{/g, '\\begin{')
                               .replace(/\\begin\{([^}]+?)\}\s*\\\\/g, '\\begin{$1}')
                               .replace(/\\\\\s*\\end\{/g, '\\end{')
                               .replace(/\\end\{([^}]+?)\}\s*\\\\/g, '\\end{$1}')
                               .replace(/\\\\\s*\\item/g, '\\item')
                               .replace(/\\item\s*\\\\/g, '\\item');

            // Process choices environment (exam-zh-choices)
            tempText = tempText.replace(/\\begin\{choices\}([\s\S]*?)\\end\{choices\}/g, function(match, inner) {
                const items = inner.split(/\\item/).map(item => item.trim()).filter(item => item.length > 0);
                const labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
                let maxLen = 0;
                const hasImages = items.some(item => /!\[[^\]]*\]\([^)]+\)/.test(item));
                const fourImageOptions = items.length === 4 && items.every(item =>
                    /^!\[[^\]]*\]\([^)]+\)$/.test(item.trim())
                );
                items.forEach(item => {
                    const approxText = item.replace(/!\[[^\]]*\]\([^)]+\)/g, '')
                        .replace(/@@MATH_PLACEHOLDER_\d+@@/g, '********');
                    if (approxText.length > maxLen) {
                        maxLen = approxText.length;
                    }
                });

                let gridCols = "grid-cols-4";
                if (maxLen > 24) {
                    gridCols = "grid-cols-1";
                } else if ((hasImages && !fourImageOptions) || maxLen > 10) {
                    gridCols = "grid-cols-2";
                }

                const preferredColumns = gridCols === 'grid-cols-1' ? 1 : (gridCols === 'grid-cols-2' ? 2 : 4);
                let html = `<div class="grid ${gridCols} gap-2 my-2 select-none choices-grid ${hasImages ? 'choices-has-images' : ''} items-baseline" data-preferred-columns="${preferredColumns}">`;
                items.forEach((item, idx) => {
                    const label = labels[idx] || (idx + 1);
                    let cleanItem = item;
                    // Auto-wrap LaTeX math macros (e.g. \dfrac{5}{2}) in choices option if missing $
                    if (/\\(dfrac|frac|sqrt|cdot|times|pm|le|ge|ne|in|vec|mathbf|boldsymbol|mathrm|text|alpha|beta|gamma|delta|theta|pi|varphi|omega)\b/.test(cleanItem) && !/\$/.test(cleanItem)) {
                        cleanItem = '$' + cleanItem + '$';
                    }
                    const imageOnly = /^!\[[^\]]*\]\([^)]+\)$/.test(item.trim());
                    html += `<div class="choices-item ${imageOnly ? 'choices-image-item' : ''} flex items-baseline"><span class="choices-label font-bold mr-1.5 text-slate-800 shrink-0">${label}.</span><div class="choices-content flex-1 [&>p]:m-0 [&>p]:inline">${cleanItem}</div></div>`;
                });
                html += '</div>';
                return html;
            });

            function splitLatexTableParts(source, mode) {
                const parts = [];
                let current = "";
                let braceDepth = 0;
                for (let i = 0; i < source.length; i++) {
                    const ch = source[i];
                    const next = source[i + 1];
                    if (ch === "\\") {
                        if (mode === "rows" && braceDepth === 0 && next === "\\") {
                            parts.push(current);
                            current = "";
                            i++;
                            continue;
                        }
                        if (mode === "rows" && braceDepth === 0 && source.slice(i, i + 3) === "\\cr") {
                            parts.push(current);
                            current = "";
                            i += 2;
                            continue;
                        }
                        current += ch;
                        if (i + 1 < source.length) {
                            current += source[i + 1];
                            i++;
                        }
                        continue;
                    }
                    if (ch === "{") braceDepth++;
                    else if (ch === "}" && braceDepth > 0) braceDepth--;
                    if (mode === "cells" && ch === "&" && braceDepth === 0) {
                        parts.push(current);
                        current = "";
                    } else {
                        current += ch;
                    }
                }
                parts.push(current);
                return parts;
            }

            function readLatexGroup(source, startIndex) {
                if (source[startIndex] !== "{") return null;
                let depth = 0;
                for (let i = startIndex; i < source.length; i++) {
                    if (source[i] === "\\" && i + 1 < source.length) {
                        i++;
                        continue;
                    }
                    if (source[i] === "{") depth++;
                    else if (source[i] === "}") {
                        depth--;
                        if (depth === 0) {
                            return { value: source.slice(startIndex + 1, i), end: i + 1 };
                        }
                    }
                }
                return null;
            }

            function parseLeadingLatexCommand(source, command, groupCount) {
                const trimmed = source.trim();
                const prefix = "\\" + command;
                if (!trimmed.startsWith(prefix)) return null;
                let cursor = prefix.length;
                const groups = [];
                for (let i = 0; i < groupCount; i++) {
                    while (/\s/.test(trimmed[cursor] || "")) cursor++;
                    const group = readLatexGroup(trimmed, cursor);
                    if (!group) return null;
                    groups.push(group.value);
                    cursor = group.end;
                }
                return { groups: groups, remainder: trimmed.slice(cursor).trim() };
            }

            function unescapeTableCellForHtml(value) {
                return value.replace(/\\textbackslash\{\}/g, "&#92;")
                            .replace(/\\textdollar\{\}/g, "$")
                            .replace(/\\textasciicircum\{\}/g, "^")
                            .replace(/\\textasciitilde\{\}/g, "~")
                            .replace(/\\&/g, "&amp;")
                            .replace(/\\%/g, "%")
                            .replace(/\\#/g, "#")
                            .replace(/\\_/g, "_")
                            .replace(/\\\{/g, "{")
                            .replace(/\\\}/g, "}");
            }

            // Process tabular environments with brace-aware cell splitting and
            // native colspan/rowspan support for Word merged cells.
            tempText = tempText.replace(/\\begin\{tabular\*?\}\s*\{([^}]*?)\}([\s\S]*?)\\end\{tabular\*?\}/g, function(match, colSpec, inner) {
                const alignList = [];
                let colIdx = 0;
                for (let i = 0; i < colSpec.length; i++) {
                    const ch = colSpec[i].toLowerCase();
                    if (ch === "l" || ch === "c" || ch === "r" || ch === "p" || ch === "m") {
                        alignList[colIdx] = ch === "l" ? "text-left" : (ch === "r" ? "text-right" : "text-center");
                        colIdx++;
                    }
                }

                const normalizedInner = inner.replace(/\\\\\[[^\]]*?\]/g, "\\\\");
                const rawRows = splitLatexTableParts(normalizedInner, "rows");
                let activeRowspans = [];
                let html = '<div class="overflow-x-auto my-3 max-w-full text-center select-none"><table class="inline-table mx-auto text-xs text-slate-700 dark:text-slate-200 border-collapse border border-slate-300 dark:border-slate-600 bg-slate-50/60 dark:bg-slate-800/40 rounded-lg shadow-sm"><tbody>';

                rawRows.forEach((rowStr) => {
                    const trimmed = rowStr.trim();
                    if (!trimmed) return;

                    const isTopRule = /\\toprule/.test(trimmed);
                    const isMidRule = /\\midrule/.test(trimmed);
                    const isBottomRule = /\\bottomrule/.test(trimmed);
                    const cleanRow = trimmed.replace(/\\(?:hline|toprule|midrule|bottomrule)\b/g, "")
                                            .replace(/\\cline\s*\{[^}]*?\}/g, "")
                                            .trim();
                    if (!cleanRow) return;

                    let rowClass = "border-b border-slate-300 dark:border-slate-700 hover:bg-slate-100/40 dark:hover:bg-slate-700/30 transition-colors";
                    if (isTopRule) rowClass += " border-t-2 border-t-slate-800 dark:border-t-slate-200";
                    if (isMidRule) rowClass += " border-b-2 border-b-slate-600 dark:border-b-slate-400";
                    if (isBottomRule) rowClass += " border-b-2 border-b-slate-800 dark:border-b-slate-200";

                    html += '<tr class="' + rowClass + '">';
                    const rawCells = splitLatexTableParts(cleanRow, "cells");
                    const nextActiveRowspans = activeRowspans.map((count) => Math.max(0, count - 1));
                    let currentCol = 0;

                    rawCells.forEach((cellStr) => {
                        let cell = cellStr.trim();
                        let colspan = 1;
                        let rowspan = 1;
                        let alignClass = alignList[currentCol] || "text-center";

                        const multicolumn = parseLeadingLatexCommand(cell, "multicolumn", 3);
                        if (multicolumn) {
                            colspan = parseInt(multicolumn.groups[0], 10) || 1;
                            const spec = multicolumn.groups[1].toLowerCase();
                            if (spec.includes("l")) alignClass = "text-left";
                            else if (spec.includes("r")) alignClass = "text-right";
                            else alignClass = "text-center";
                            cell = (multicolumn.groups[2] + multicolumn.remainder).trim();
                        }

                        const multirow = parseLeadingLatexCommand(cell, "multirow", 3);
                        if (multirow) {
                            rowspan = parseInt(multirow.groups[0], 10) || 1;
                            cell = (multirow.groups[2] + multirow.remainder).trim();
                        }

                        let coveredByPriorRow = true;
                        for (let offset = 0; offset < colspan; offset++) {
                            if (!(activeRowspans[currentCol + offset] > 0)) {
                                coveredByPriorRow = false;
                                break;
                            }
                        }
                        if (!cell && coveredByPriorRow) {
                            currentCol += colspan;
                            return;
                        }

                        if (rowspan > 1) {
                            for (let offset = 0; offset < colspan; offset++) {
                                nextActiveRowspans[currentCol + offset] = rowspan - 1;
                            }
                        }

                        cell = normalizeNakedMathForPreview(unescapeTableCellForHtml(cell));
                        const borderClass = "border border-slate-300 dark:border-slate-700";
                        const rowspanAttr = rowspan > 1 ? ' rowspan="' + rowspan + '"' : "";
                        html += '<td colspan="' + colspan + '"' + rowspanAttr + ' class="px-3 py-1.5 ' + borderClass + ' ' + alignClass + ' font-normal align-middle">' + cell + '</td>';
                        currentCol += colspan;
                    });

                    activeRowspans = nextActiveRowspans;
                    html += '</tr>';
                });

                html += '</tbody></table></div>';
                return html;
            });

            tempText = tempText.replace(/\\begin\{center\}/g, '<div class="text-center my-1">')
                               .replace(/\\end\{center\}/g, '</div>')
                               .replace(/\\item\s*\[([^\]]+?)\]/g, '</li><li class="my-0.5 list-none -ml-4">$1 ')
                               .replace(/\\item/g, '</li><li class="my-0.5">')
                               .replace(/\\begin\{itemize\}/g, '<ul class="list-disc pl-4 my-1">')
                               .replace(/\\begin\{enumerate\}/g, '<ol class="list-decimal pl-4 my-1">')
                               .replace(/\\end\{itemize\}/g, '</li></ul>')
                               .replace(/\\end\{enumerate\}/g, '</li></ol>')
                               .replace(/<ul class="list-disc pl-4 my-1">\s*<\/li>/g, '<ul class="list-disc pl-4 my-1">')
                               .replace(/<ol class="list-decimal pl-4 my-1">\s*<\/li>/g, '<ol class="list-decimal pl-4 my-1">');

            // Process LaTeX bold formatting outside math environments
            tempText = tempText.replace(/\\textbf\s*\{([^{}]*?)\}/g, '<strong>$1</strong>');

            // Replace spacing commands outside math blocks with non-breaking spaces for a clean sidebar preview
            tempText = tempText.replace(/\\\\qquad/g, '&nbsp;&nbsp;&nbsp;&nbsp;')
                               .replace(/\\\\quad/g, '&nbsp;&nbsp;')
                               .replace(/\\qquad/g, '&nbsp;&nbsp;&nbsp;&nbsp;')
                               .replace(/\\quad/g, '&nbsp;&nbsp;')
                               .replace(/(\\+) /g, function(match, slashes) {
                                   // Only an unpaired final slash is a TeX space;
                                   // paired slashes remain available as hard breaks.
                                   return slashes.length % 2 === 0 ? match : slashes.slice(0, -1) + '&nbsp;';
                               });
                               
            // Replace LaTeX line breaks with HTML br tags outside math environments
            tempText = tempText.replace(/\\\\/g, '<br>');

            // ---- Markdown-lite prose conversions ---------------------------------
            // Runs after raw HTML tags are stripped (see the `<[^>]*>` pass
            // above) and before math placeholders / literal markers are restored
            // at the end of this function, so it only ever sees prose. Math
            // placeholders (@@MATH_PLACEHOLDER_n@@) and literal markers
            // (\uE002L..\uE003) carry no markdown metacharacters, so formulas and
            // inline code stay inert. This is the link channel referenced by
            // previewLiteralPattern: [label](http(s)://…) and bare http(s) URLs
            // become anchors here.
            function markdownEscapeAttr(value) {
                return escapeHtml(String(value == null ? '' : value))
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#039;')
                    .replace(/\r?\n/g, ' ');
            }

            function buildMarkdownAnchor(href, label) {
                const target = String(href == null ? '' : href).trim();
                let valid = false;
                try {
                    const parsed = new URL(target, window.location.href);
                    valid = parsed.protocol === 'http:' || parsed.protocol === 'https:';
                } catch (error) {
                    valid = false;
                }
                const safeLabel = escapeHtml(String(label == null ? '' : label));
                // Reject anything that is not plain http(s): the label degrades
                // to inert text so no javascript:/data: scheme can slip through.
                if (!valid) return safeLabel;
                return '<a href="' + markdownEscapeAttr(target)
                    + '" target="_blank" rel="noopener noreferrer">' + safeLabel + '</a>';
            }

            function convertLiteMarkdown(rawText) {
                // Protect image markdown first so the link / bare-URL passes do
                // not rewrite an image's target URL. Images are restored verbatim
                // and then handled by the existing <img> conversion below.
                const imagePayloads = [];
                const imagePrefix = '\uE004IMG';
                const guardedText = String(rawText).replace(/!\[[^\]\n]*\]\([^\n)]*\)/g, (match) => {
                    const marker = imagePrefix + imagePayloads.length + '\uE005';
                    imagePayloads.push({ marker, original: match });
                    return marker;
                });

                const lines = guardedText.split('\n');
                const out = [];
                const tableRowRe = /^\s*\|.*\|/;
                const cellCount = (line) => (line.match(/\|/g) || []).length;
                // A divider row contains only pipes, dashes, colons and spaces
                // (so a data row such as `| a-b | c |` is not misread as one).
                const isDividerRow = (line) => /^[\s|:-]+$/.test(line)
                    && line.includes('-') && line.includes('|');
                const splitCells = (line) => {
                    let trimmed = line.trim();
                    if (trimmed.charAt(0) === '|') trimmed = trimmed.slice(1);
                    if (trimmed.charAt(trimmed.length - 1) === '|') trimmed = trimmed.slice(0, -1);
                    return trimmed.split('|').map((cell) => cell.trim());
                };
                const ulRe = /^\s{0,3}[-*]\s+(.+)$/;
                const olRe = /^\s{0,3}\d+[.)]\s+(.+)$/;
                const padCells = (cells, count) => {
                    const padded = cells.slice(0, count);
                    while (padded.length < count) padded.push('');
                    return padded;
                };

                let index = 0;
                while (index < lines.length) {
                    const line = lines[index];

                    // Pipe table: consecutive lines starting with `|` that
                    // contain at least two `|`.
                    if (tableRowRe.test(line) && cellCount(line) >= 2) {
                        const block = [line];
                        let next = index + 1;
                        while (next < lines.length && tableRowRe.test(lines[next]) && cellCount(lines[next]) >= 2) {
                            block.push(lines[next]);
                            next++;
                        }
                        let bodyStart = 1;
                        if (block.length >= 2 && isDividerRow(block[1])) {
                            bodyStart = 2;
                        }
                        const headerCells = splitCells(block[0]);
                        const bodyRows = block.slice(bodyStart).map(splitCells);
                        let columnCount = Math.max(1, headerCells.length);
                        bodyRows.forEach((row) => { columnCount = Math.max(columnCount, row.length); });
                        let html = '<table class="mb-preview-markdown-table my-2 max-w-full border-collapse"><thead><tr>';
                        padCells(headerCells, columnCount).forEach((cell) => {
                            html += '<th class="border border-slate-300 dark:border-slate-600 px-2 py-1 text-left bg-slate-50 dark:bg-slate-800 font-bold">' + cell + '</th>';
                        });
                        html += '</tr></thead><tbody>';
                        bodyRows.forEach((row) => {
                            html += '<tr>';
                            padCells(row, columnCount).forEach((cell) => {
                                html += '<td class="border border-slate-300 dark:border-slate-600 px-2 py-1 align-top">' + cell + '</td>';
                            });
                            html += '</tr>';
                        });
                        html += '</tbody></table>';
                        out.push(html);
                        index = next;
                        continue;
                    }

                    // Unordered list.
                    if (ulRe.test(line)) {
                        const items = [];
                        let next = index;
                        while (next < lines.length) {
                            const item = ulRe.exec(lines[next]);
                            if (!item) break;
                            items.push(item[1]);
                            next++;
                        }
                        out.push('<ul class="list-disc pl-4 my-1">'
                            + items.map((text) => '<li class="my-0.5">' + text + '</li>').join('') + '</ul>');
                        index = next;
                        continue;
                    }

                    // Ordered list.
                    if (olRe.test(line)) {
                        const items = [];
                        let next = index;
                        while (next < lines.length) {
                            const item = olRe.exec(lines[next]);
                            if (!item) break;
                            items.push(item[1]);
                            next++;
                        }
                        out.push('<ol class="list-decimal pl-4 my-1">'
                            + items.map((text) => '<li class="my-0.5">' + text + '</li>').join('') + '</ol>');
                        index = next;
                        continue;
                    }

                    out.push(line);
                    index++;
                }

                let result = out.join('\n');

                // **bold** before links so a bold span that also holds a link
                // keeps the generated anchor intact.
                result = result.replace(/\*\*([\s\S]+?)\*\*/g, (match, inner) =>
                    '<strong>' + escapeHtml(inner) + '</strong>');

                // [label](http://…) -> anchor. Inline code never reaches here as
                // backticks are already literal markers, so no double wrapping.
                result = result.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
                    (match, label, href) => buildMarkdownAnchor(href, label));

                // Bare http(s) URLs, skipping anything already inside a generated
                // href (always preceded by a quote) and trailing punctuation.
                // CJK characters terminate the URL so prose like
                // "链接https://x 结束" keeps its trailing text.
                result = result.replace(/(^|[^"=/\w])(https?:\/\/[^\s<>"'（）()【】\[\]\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+)/g,
                    (match, prefix, url) => {
                        let trailing = '';
                        let core = url;
                        const tail = /[.,;:!?、。，；：！？]+$/.exec(url);
                        if (tail) {
                            trailing = tail[0];
                            core = url.slice(0, -tail[0].length);
                        }
                        if (!core) return match;
                        return prefix + buildMarkdownAnchor(core, core) + trailing;
                    });

                imagePayloads.forEach(({ marker, original }) => {
                    result = result.split(marker).join(original);
                });
                return result;
            }

            tempText = convertLiteMarkdown(tempText);
            
            // A paragraph starts a new line with a short visual gap; two BRs
            // would additionally create a whole empty text line in Word answers.
            const paragraphBreak = '<span class="mb-preview-paragraph-break" aria-hidden="true"></span>';
            // Keep subquestions on distinct paragraphs without adding a second
            // gap when the source already has a blank line or a hard break.
            tempText = tempText.replace(/(?:\r?\n|\s+|([。；;!！\.])\s*)([(（]?(?:[1-9]|10|[ivxIVX]+|[①②③④⑤⑥⑦⑧⑨⑩])[)）\.]|\([1-9]\)|（[1-9]）|\([ivxIVX]+\)|（[ivxIVX]+）)(?=\s*[\u4e00-\u9fa5a-zA-Z\$])/g, function(match, punctuation, marker) {
                return (punctuation || '') + paragraphBreak + marker + ' ';
            });

            // 双回车起新段落；单回车保留为可见换行（渲染层修复：此前被折叠成
            // 空格，导致 Word/PDF 解析出的段内分行全部丢失）；显式 \\\\ 仍为硬换行。
            // 单回车落到 <br> 而非空格，可复用下方 3799-3804 既有的
            // “<br> 与 paragraphBreak 相邻收敛”规则，不会产生多余空行。
            tempText = tempText.replace(/\r\n/g, '\n')
                               .replace(/\n\n+/g, paragraphBreak)
                               // 显式硬换行（\\\\ -> <br>）后再跟一个物理换行，只应
                               // 渲染成一次换行。先吞掉这个换行，否则下面把 \n 也
                               // 变成 <br> 会得到 <br><br>，即凭空多出一个空行。
                               // 仅吃紧随其后的单个换行：\n\n+ 上一步已变成段落
                               // 分隔，此处不可能出现连续换行。
                               .replace(/(<br>)[ \t]*\n/g, '$1')
                               // 块级产物（表格/列表/图片/选项）自带外边距，紧贴它的
                               // 那个换行同样不该再变成 <br>，否则会多出一行的空白。
                               // 这里在 \n -> <br> 之前处理，所以只影响换行产生的
                               // <br>，显式 \\\\ 的硬换行不受影响。
                               .replace(/[ \t]*\n(?=<(?:div|table|ul|ol)\b)/g, '')
                               .replace(/(<\/(?:div|table|ul|ol)>)[ \t]*\n/g, '$1')
                               // 图片此刻还是 ![alt](src)，下一句才变成自带外边距的
                               // <div>，所以这里按 markdown 形态同样吞掉紧贴的换行。
                               .replace(/(\!\[[^\]\n]*\]\([^\n)]*\))[ \t]*\n/g, '$1')
                               .replace(/\n(?=!\[[^\]\n]*\]\([^\n)]*\))/g, '')
                               .replace(/\n/g, '<br>');
                               
            // 转换 Markdown 题目插图与配图语法 ![](/static/uploads/xxx.png) 为精美自适应预览图
            tempText = tempText.replace(/!\[(.*?)\]\(([^)]+)\)/g, function(match, alt, src) {
                const safeSrc = window.MathBankSafe.safeImageUrl(src);
                if (!safeSrc) return '';
                const safeAlt = window.MathBankSafe.escapeAttribute(alt || '题目配图');
                const key = window.ImageLayoutTools ? window.ImageLayoutTools.key(src) : src;
                const layout = Object.prototype.hasOwnProperty.call(imageLayouts || {}, key) ? imageLayouts[key] : null;
                const align = layout && ['left', 'center', 'right'].includes(layout.align) ? layout.align : 'center';
                const size = layout && ['auto', 'small', 'medium', 'large'].includes(layout.size) ? layout.size : 'auto';
                return `<div class="my-2.5 text-center mb-inline-image-align-${align}"><img src="${window.MathBankSafe.escapeAttribute(safeSrc)}" alt="${safeAlt}" class="mb-inline-image-size-${size} max-w-[220px] max-h-[180px] object-contain rounded-lg border border-slate-200 shadow-sm inline-block cursor-zoom-in hover:shadow-sm hover:scale-[1.02] transition-all" data-safe-image-open="true" title="点击在新标签页查看高清原图"></div>`;
            });

            // Generated block content already owns its vertical spacing. Keep
            // image/choice/table anchors and display math in place, but do not
            // stack a paragraph spacer on their existing block margins.
            tempText = tempText
                .replace(new RegExp('(?:' + paragraphBreak + '\\s*){2,}', 'g'), paragraphBreak)
                .replace(new RegExp('(?:<br>\\s*)+' + paragraphBreak, 'g'), paragraphBreak)
                .replace(new RegExp(paragraphBreak + '(?:\\s*<br>)+', 'g'), paragraphBreak)
                .replace(new RegExp(paragraphBreak + '\\s*(?=<(?:div|table|ul|ol)\\b)', 'g'), '')
                .replace(new RegExp('(</(?:div|table|ul|ol)>)\\s*' + paragraphBreak, 'g'), '$1')
                .replace(new RegExp('^(?:\\s*' + paragraphBreak + ')+|(?:' + paragraphBreak + '\\s*)+$', 'g'), '');
            placeholders.forEach(({placeholder, original}) => {
                if (!/^(?:\$\$|\\\[)/.test(original)) return;
                tempText = tempText
                    .replace(new RegExp(paragraphBreak + '\\s*' + placeholder, 'g'), placeholder)
                    .replace(new RegExp(placeholder + '\\s*' + paragraphBreak, 'g'), placeholder);
            });
                               
            // Restore math blocks with HTML escaping
            function escapeHtml(str) {
                return str.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;');
            }

            // Adjacent inline math such as $a$$b$$c$ can make a later
            // placeholder contain an earlier one. Restore in LIFO order so
            // the outer placeholder is expanded before its nested token.
            placeholders.slice().reverse().forEach(({ placeholder, original }) => {
                tempText = tempText.replace(placeholder, () => escapeHtml(original));
            });
            literalEntries.forEach(({marker, visible}) => {
                // DOMPurify allows code, and every KaTeX auto-render entrance
                // ignores code by default. Keep the contents inert and exact.
                const html = '<code class="mb-preview-literal">' + escapeHtml(visible) + '</code>';
                tempText = tempText.split(marker).join(html);
            });
            
            return tempText;
        }
        window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;
            
        function parseMarkdownWithMath(text, imageLayouts = {}) {
            if (!text) return "";
            return window.MathBankSafe.sanitizeRichHtml(preprocessFormulaForKaTeX(text, imageLayouts));
        }
        window.parseMarkdownWithMath = parseMarkdownWithMath;

        function renderQuestionPreviewContent(container, text, options = {}) {
            if (!container) return;
            const settings = options && typeof options === 'object' ? options : {};
            const includeImages = settings.includeImages !== false;
            let preparedHtml;
            if (typeof settings.preparedHtml === 'string') {
                preparedHtml = window.MathBankSafe.sanitizeRichHtml(settings.preparedHtml);
                if (!includeImages) {
                    preparedHtml = preparedHtml.replace(/<img\b[^>]*>/gi, '');
                }
            } else {
                let source = String(text || '');
                if (!includeImages) {
                    source = source
                        .replace(/!\[[^\]\n]*\]\(\s*(?:<[^>\n]*>|[^\s)]+)(?:\s+(?:"[^"\n]*"|'[^'\n]*'))?\s*\)/gi, '')
                        .replace(/<img\b[^>]*>/gi, '');
                }
                preparedHtml = parseMarkdownWithMath(source, settings.imageLayouts || {});
            }
            container.innerHTML = preparedHtml;
            try {
                renderMathInElement(container, {
                    delimiters: [
                        {left: '$$', right: '$$', display: true},
                        {left: '$', right: '$', display: false},
                        {left: '\\(', right: '\\)', display: false},
                        {left: '\\[', right: '\\]', display: true}
                    ],
                    throwOnError: false
                });
            } catch (error) {
                console.error('KaTeX question preview rendering error: ', error);
            }
            adaptChoicesGridLayout(container);
            return preparedHtml;
        }
        window.renderQuestionPreviewContent = renderQuestionPreviewContent;

        // Format raw OCR questions by detecting choice options and introducing nice line breaks
        function formatQuestionContent(text) {
            if (!text) return "";
            
            // Strip the LaTeX negative space command "\!" and thin space "\," which are cluttering
            let formatted = text.replace(/\\!/g, '').replace(/\\,/g, '');
            
            // 1. Check if it is actually a choice question with options A, B, C, D
            const hasA = /[\s,，、]*\bA(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasB = /[\s,，、]*\bB(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasC = /[\s,，、]*\bC(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasD = /[\s,，、]*\bD(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const isChoiceQuestion = hasA && hasB && hasC && hasD;
            
            // Protect math blocks from being replaced, and clean up formula-level exclamation noise
            const parts = formatted.split(/(\$\$[\s\S]*?\$\$|\$[^\$]+?\$)/g);
            for (let i = 0; i < parts.length; i++) {
                // If it is a math block (odd indices in split result)
                if (i % 2 === 1) {
                    // 1. Remove all Chinese full-width exclamation marks "！" inside math
                    parts[i] = parts[i].replace(/！/g, '');
                    // 2. Remove all standalone half-width exclamation marks "!" not preceded by numbers or letters
                    parts[i] = parts[i].replace(/(^|[^0-9a-zA-Z\)\}\]])!/g, '$1');
                    // 3. Remove any remaining negative spacing "\!" just in case
                    parts[i] = parts[i].replace(/\\!/g, '');
                    // 4. Remove all LaTeX thin spaces "\,"
                    parts[i] = parts[i].replace(/\\,/g, '');
                } else {
                    // Only modify non-math blocks (even indices in split result)
                    if (isChoiceQuestion) {
                        // Replace option labels with clean newlines and normalized format
                        parts[i] = parts[i]
                            .replace(/[\s,，、]*\b([A-D])(?:[\.\s、，．]+)(?!\$)/g, '\n\n$1. ')
                            .replace(/[\s,，、]*\(([A-D])\)(?!\$)/g, '\n\n($1) ')
                            .replace(/[\s,，、]*（([A-D])）(?!\$)/g, '\n\n($1) ');
                    }
                }
            }
            formatted = parts.join('');
            
            // Clean up any leading/trailing duplicate newlines
            formatted = formatted.replace(/\n{3,}/g, '\n\n').trim();
            
            return formatted;
        }

        // 🟢 智能心跳循环：每 15 秒向后台发送一次轻量级心跳
        // 只要题库页面（有任意标签页）处于打开状态，后台的 1 小时闲置自杀机制就不会被触发
        setInterval(() => {
            fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
        }, 15000);
