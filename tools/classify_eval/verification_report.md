# 独立验证报告（verifier）——Phase 2 分类改造

> 立场：**证伪**，不背书。
> 被测代码快照：`mathbank/classify_rules.py` md5 `4de8cc82…`（36912 B）→ 复核时已变为 `01c875fb…`，
> 两次快照下留出集数字**完全一致**（41/48），结论不受影响。
> 我只新增了 `tools/classify_eval/` 下的文件，未改动 `mathbank/`、`main.py`、`static/`，未 commit。

## 1. 留出集 vs 原集（核心产出）

留出集 `tools/classify_eval/holdout_cases.json`：48 条全新高中数学题，四类各 12 条，
latex 27 / plaintext 21，其中 40 条带 `tricky` 标记。
与 `cases.json` 的 4-gram Jaccard 最高 **0.257**（全部 < 0.30），无雷同；
知识点优先覆盖原集未涉足的：空间向量与立体几何、复数几何意义、计数原理/排列组合、
条件概率、随机变量（超几何/正态）、成对数据统计（回归/列联表/独立性检验）、
数学归纳法、直线与圆、导数与不等式综合。

| 指标 | 原集 66（调参集） | 留出集 48（未见） | 变化 |
| --- | --- | --- | --- |
| 细粒度四值准确率·情形A（AI 给理想四值） | **66/66 = 100.0%** | **41/48 = 85.4%** | −14.6pt |
| 细粒度四值准确率·情形B（20% 噪声） | 66/66 = 100.0% | 41/48 = 85.4% | −14.6pt |
| 　单选题 | 16/16 = 100% | 12/12 = 100% | 0 |
| 　多选题 | 15/15 = 100% | **8/12 = 66.7%** | −33.3pt |
| 　填空题 | 17/17 = 100% | 12/12 = 100% | 0 |
| 　解答题 | 18/18 = 100% | **9/12 = 75.0%** | −25.0pt |
| 单选 vs 多选取分（情形A） | 31/31 = 100% | 20/24 = 83.3% | −16.7pt |
| 选项误判 FP（填空/解答判有选项） | 0/35 = 0.0% | **3/24 = 12.5%** | +12.5pt |
| 选项漏判 FN（单选/多选判无选项） | 0/31 = 0.0% | 0/24 = 0.0% | 0 |
| 填空位命中 | 17/17 = 100% | 12/12 = 100% | 0 |
| `detect_structured_question_form` 命中率 | 48/66 = 72.7% | 39/48 = 81.2% | +8.5pt |
| 章节 top-1 | 18/66 = 27.3% | 12/48 = **25.0%** | −2.3pt |
| 章节 top-3 | 30/66 = 45.5% | 23/48 = 47.9% | +2.4pt |
| 章节 top-8 | 48/66 = 72.7% | 30/48 = **62.5%** | −10.2pt |
| 难度先验 完全一致 | 43/66 = 65.2% | 21/48 = **43.8%** | −21.4pt |
| 难度先验 ±1 档 | 66/66 = 100% | 47/48 = 97.9% | −2.1pt |

**结论：100% 是过拟合的产物，真实泛化水平约 85%。** 多选题与解答题是重灾区；
单选与填空题的 100% 是真的（结构层对 A-D / 空位的识别很稳）。

## 2. 留出集上判错的每一条（情形A 与情形B 完全相同，7 条）

| # | ID | 期望 | 实际 | 判定来源 | 根因 |
| --- | --- | --- | --- | --- | --- |
| 1 | `MC-H01` | multi_choice | single_choice | structure | 题干**无任何多选提示语**（正方体向量，A/B/C/D 全真）。`detect_multi_choice_signals` 只匹配提示语模板，读不出"正确项多于一个" |
| 2 | `MC-H05` | multi_choice | single_choice | structure | 同上（正态分布，A/B/D 真、C 假） |
| 3 | `MC-H09` | multi_choice | single_choice | structure | 同上（圆与圆位置关系，A/B/D 真、C 假） |
| 4 | `MC-H12` | multi_choice | single_choice | structure | 同上（导数 $f(x)=xe^x$，四项全真） |
| 5 | `DA-H08` | detailed_answer | single_choice | structure | 解答题小问用 **①②③** 编号 → `_CIRCLED_NUMBERS` 被 `analyze_options` 当作圈码选项（≥3 个即成立），且 `_strong_option_evidence` 对 circled/marker 直接放行 |
| 6 | `DA-H09` | detailed_answer | single_choice | structure | 解答题小问用 `\begin{enumerate}` + 3 个 `\item` → "macro" 类证据 → 判选择题 |
| 7 | `DA-H10` | detailed_answer | single_choice | structure | 题干「**甲、乙、丙、丁**、戊 5 名同学」——天干是人名却紧跟顿号，被天干选项标记规则命中 |

附加（不计入题型准确率，但老师看得到）：

* ①②③ 还被 `detect_subquestion_count` 计入小问数 → `SC-H01`、`MC-H02` 的"小问数≥3（4）"是假的，虚增难度先验。
* 4 条章节检索**完全为空**（`SC-H02`/`SC-H10`/`DA-H10`/`DA-H11`）→ `chapter_code=""`、`chapter_source="fallback"`。
  没乱填 B1-C1 是对的，但老师也拿不到任何候选。

## 3. 反作弊审查

* **无按 id 特判**：`classify_rules.py` / `question_types.py` 中不存在 `SC-LATEX-03` 之类字符串，
  不存在 `case.get('id')` 分支，也不存在任何读取 `cases.json` 的代码路径。✅
* **术语表不是抄用例词**：`_TERM_HINTS` 共 130 条，只有 53 条（40.8%）在 `cases.json` 题干里出现过，
  其余 77 条（交集/并集/弧度制/二面角/超几何分布/列联表…）是新增的。✅
* **但覆盖率不足**：160 个课程节点里有 **58 个（36%）** 没有任何术语能直接命中，
  包括 `X3-C8-S1`(成对数据相关性)、`X1-C1-S2`(空间向量基本定理)、`B1-C5-S7`、`X1-C2-S3-P1` 等。
* **过宽的面层提示（点名）**：
  * `(r"f\s*\(\s*x\s*\)|函数", ("B1-C3",))` —— 任何出现"函数"或 `f(x)` 的题都给必修一第三章加分。
    留出集中 `MC-H12`(导数)、`FB-H08`(复合函数求导) 的 top-3 都被 `B1-C3` 占据；
    纯英文题干、5000 字超长文本的兜底章节也一律落到 `B1-C3`。
  * `(r"a\^\{?\s*x\s*\}?|\\?\^ ?\{?x\}?\}", ("B1-C4-S2",))` —— `e^{x}` 被当成指数函数（`MC-H12` top-3 出现 `B1-C4-S2`）。
  * 术语歧义：`集合` 是普通词，`DA-H01` 里"求 $a$ 的取值**集合**"直接把 top-1 拉到 `B1-C1`。
* **难度阈值**：`score>=4 → hard`、`>=2 → medium`，未发现"刚好卡住某条用例"的痕迹；
  但有结构性缺陷（圈码选项被当小问计数、选项文本里的"恒成立/证明"计入设问词）。
* **结论**：不是硬编码作弊，是**特征工程能力不足 + 若干过宽规则**。

## 4. 集成检查（读代码，未起服务）

| # | 检查项 | 结论 |
| --- | --- | --- |
| 1 | 是否真调用 `classify_with_rules` | ✅ 通过。`main.py:4213` 正常融合、`main.py:4205` 降级；旧的单向纠偏 `choice → detailed_answer` 已无残留（grep `normalize_ai_question_form`/`detect_choice_options` 在 main.py 中 0 命中） |
| 2 | 是否删除 `chapter_options[0]`（B1-C1）静默回退 | ✅ 通过。全仓 grep 无 `B1-C1` 兜底；检索为空时 `chapter_code=""`、`chapter_source="fallback"` |
| 3 | 失败路径返回 200 + `status="partial"` | ✅ 通过（两条 AI 失败路径均 200/partial）。⚠️ 另有一条"未配置 API Key"返回 **400**，但响应体仍是 JSON，前端 `.then(r=>r.json())` 能读到 `message` 并 toast，不会静默失败——可接受 |
| 4 | `chapter_code` 空值为 `""` | ✅ 通过（不是 `"null"` / `"未知"` / `None`） |
| 5 | 返回体含前端依赖的全部字段 | ✅ 通过。13 项全在，另多出 `question_type_candidates`、`difficulty_prior` |
| 6 | `evidence` 是否可直读中文 | ✅ 基本通过。`multi_signals` = `["题干标注「多选题」"]`，`options.evidence` = `["识别到选项字母 A、B、C、D"]`，`difficulty_signals` 为中文。残留：`difficulty_signals` 里 `关键词:最大值` 用半角冒号（可读，非阻塞）；`options.letters` 是数组 `["A","B","C","D"]`，前端有自己的映射未直接渲染 |
| 7 | 是否新增 `review_reasons` | ❌ **未新增**。全仓 `review_reasons` 只出现在 `pdf_figures.py`，与分类端点无关 |
| 8 | `question_type_source==='fallback'` 与 `is_fallback` 是否语义重叠 | ✅ 已收窄为 `is_fallback = (type==unknown and chapter_source=='fallback')`，不再重叠。⚠️ 但 `main.py:4206/4220` 降级路径又手动置 `is_fallback=True`，与函数内定义形成两套含义（"整包降级" vs "两项都没定出来"），建议统一成一个字段名 |

## 5. 回归与边界

* `tests/test_question_types.py` 的 14 条断言（5 条结构判定 + 1 条 choices 优先 + 8 条三值归一化）**全部通过** ✅
  —— `normalize_ai_question_form` 的三值语义未被改变，`paper_helper.py` / `word_export_helper.py` 不受影响。
* `detect_choice_options` 仍返回 `bool` ✅（对 `''`、`'A. 1\nB. 2\nC. 3'`、`角A、B、C的对边` 三种输入验证过类型）。
* **冷启动 import 顺序**：起 4 个全新解释器（`-W error::SyntaxWarning`）分别验证
  `classify_rules → question_types`、`question_types → classify_rules`、以及两个模块的单独导入，**全部 PASS** ✅
  无循环 import；先前报的 `"\choice"` 非法转义告警已被修掉。
* **边界/错误路径**：21 组输入（空串 / 纯空白 / None / 5000 字超长 / 纯英文 / 纯图片占位 / 图片+少量文字 /
  乱码+零宽字符 / emoji / 纯数字标点 / 极端重复 500 次 / SQL·HTML 注入串，以及
  `ai_payload` 为 `None` / `{}` / 缺字段 / `选择题` / 超纲值 / `difficulty="超难"` /
  `chapter_code="Z9-C99"` / `chapter_code=None` / 字段类型全部非法）——
  **零异常、返回体 13 个字段齐全、`chapter_code` 始终是 str** ✅
  其中 `chapter_code="Z9-C99"` 被正确拒绝并回落到规则检索 ✅
* `git diff --stat`：`main.py` +2、`mathbank/classify_rules.py` +152/−46、`static/index.html`、`static/js/import.js`。
  后两个是既有的前端改动，**我没有碰**；我只新增 `tools/classify_eval/` 下的文件 ✅

## 6. 最大的系统性风险（比准确率更严重）

规则层在 **81%**（39/48）的题目上直接说了算（`question_type_source='structure'`），
但 `needs_review` 的判据是「难度冲突 / 题型 unknown / 章节非 ai / 难度非 ai」——
**完全不看题型来源**。

实测：留出集 48 条里有 **38 条**是"规则层定的题型且 `needs_review=False`"，
其中 **6 条是错的**（含 3 条把 AI 的"解答题"整个翻成"单选题"的 180° 覆盖）。
老师看到的是一个**带着确定证据、没有任何复核提示的错误答案**。

同样的问题让 4 条"无提示语多选题"被静默判成单选题——而多选题在真实试卷里
恰恰是最容易漏写提示语的一类。

## 7. 一句话结论

**现在还不能放心交给一位高中数学老师日常使用。**
最大的风险不是 85.4% 的准确率本身，而是 **"规则层在 81% 的题上静默覆盖模型的结论，
而 `needs_review` 完全不覆盖这种覆盖行为"** —— 12.5% 的错误会以"无提示的确定答案"呈现。

按优先级，上线前至少要修：

1. `needs_review` 加入 `question_type_source in ('structure','corrected','rule')` 且规则结论与模型不一致的情形（最小改动，收益最大）。
2. 圈码 `①②③` 与 `\begin{enumerate}+\item` 作为**选项证据**前，先排除它们同时满足小问编号特征（行首 + 后跟"求/判断/证明"）的情形；并把 `detect_subquestion_count` 的圈码计数与选项扫描解耦（顺带修掉难度虚增）。
3. 天干 `甲乙丙丁` 标记要求"至少 3 个且后面紧跟的是选项内容（短、无句号）"，或要求题干中不存在"同学/名/位/人"等人称量词。
4. 多选题补一条兜底：结构层判为 choice 但无任何多选提示语时，**降为"待确认"并交给模型二次判定**，而不是默认单选题。
5. 章节：给 `X3-C8-S1`、`X1-C1-S2`、`B1-C5-S7` 等 58 个无术语节点补词；收紧 `函数|f(x)`、`^{x}` 两条过宽面层提示；`集合` 改为 `集合A|集合B|∁_U` 等带上下文的模式。
