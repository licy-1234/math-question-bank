# 最终独立验收报告（commit `53ac9f1`）

角色：证伪。本报告不改动任何业务源码（`mathbank/` 与 `main.py` 的 `git diff` 为空）。

---

## 0. md5 前后对照（证明测量期间代码未被改动）

| 文件 | BEFORE | AFTER |
| --- | --- | --- |
| `mathbank/classify_rules.py` | `90d1c23921a02aa0b3f2b340e6c198b2` | `90d1c23921a02aa0b3f2b340e6c198b2` |
| `mathbank/question_types.py` | `f515be0a3732ed387d9b47fd1e200e98` | `f515be0a3732ed387d9b47fd1e200e98` |
| `main.py` | `225b5ffa74b530ac82d4ea5a2ddc2f69` | `225b5ffa74b530ac82d4ea5a2ddc2f69` |

`git diff --stat mathbank/ main.py` 为空 —— 业务源码零改动。

---

## 1. 三集合最终数字

| 集合 | n | 情形A | 情形B | 单选/多选取分 | 选项FP | 选项FN | 填空位 | 结构命中 | 难度±1 | 难度完全一致 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cases`（调优集） | 66 | **100.0%** | **100.0%** | 31/31 | 0/35 | 0/31 | 17/17 | 48/66 = 72.7% | 66/66 = 100% | 43/66 |
| `holdout`（未见过②） | 48 | **100.0%** | **100.0%** | 24/24 | 0/24 | 0/24 | 12/12 | 36/48 = 75.0% | 47/48 = 97.9% | 20/48 |
| `final`（未见过③） | 48 | **100.0%** | **100.0%** | 24/24 | 0/24 | 0/24 | 12/12 | 36/48 = 75.0% | 46/48 = 95.8% | 20/48 |

`check_rework.py --strict`：**17/17 PASS**（R1a/R1b/R2a/R2b/R2c/R3/R4/R5/R6a/R6b/R7a/R7b/R8×4/R8z 全绿）。

章节（与你报的数字**逐位一致**）：

| 集合 | top-1 | top-3 | top-8 |
| --- | --- | --- | --- |
| cases | 19/66 = 28.8% | 33/66 = 50.0% | 48/66 = 72.7% |
| holdout | 12/48 = 25.0% | 23/48 = 47.9% | 28/48 = 58.3% |
| final | 14/48 = 29.2% | 20/48 = 41.7% | 25/48 = 52.1% |

章节 A/B（返工前 `53ac9f1~1` vs 返工后）见第 5 节。

---

## 2. 反作弊结论：**清白，没有针对测试集的硬编码**

| 检查 | 结果 |
| --- | --- |
| 是否读取任何用例文件（`open` / `json.load` / `Path(` / `cases.json`） | 无，仅 docstring 里出现一次路径字样 |
| 是否出现用例 id 字面量（`SC/MC/FB/DA-*`） | 无 |
| 是否按 id / source_hint 走分支 | 无 |
| `_TERM_HINTS` 是否抄自题面 | 179 条术语，仅 **44.1%** 在 162 条题面中出现；分集合 cases 30.2% / holdout 21.8% / final 19.0%。若是抄题面，cases 应接近 100% |

**结论：无 id 硬编码、无题面字符串硬编码、无按知识点反向拟合。**

但"没有作弊"≠"这个 100% 有意义"，见第 3 节。

---

## 3. 100% 是结构修复还是又一次过拟合？——**都不是，是指标盲区**

### 3.1 直通道测量：`情形A 输出 == 输入`

| 版本 | 输出 == 喂进去的模型值 | 来源分布 |
| --- | --- | --- |
| 返工前 | 145/155 = 93.5% | structure 120 / ai 35 |
| **返工后** | **162/162 = 100.0%** | structure 120 / ai 42 |

### 3.2 换标签反证（证明不是纯透传）

把"期望值"整体换成**随机错误标签**再喂进去，命中率只有：

| final | holdout | cases |
| --- | --- | --- |
| 25.0% | 12.5% | 7.6% |

→ 说明规则层在**「选择 vs 填空 vs 解答」这条轴上确有独立判断力**，`100%` 不是无脑回显。

### 3.3 真正的盲区在 `decide_question_type`

```python
explicit_ai = ai_type in (QUESTION_TYPE_SINGLE_CHOICE, QUESTION_TYPE_MULTI_CHOICE)   # L497
if structure == QUESTION_FORM_CHOICE:
    if explicit_ai:
        return ai_type, "structure", evidence          # L503-505 无条件采信
...
if explicit_ai:
    if options["has_options"]:
        return ai_type, "ai", evidence                 # L512-516 无条件采信
```

只要模型给了显式 `single`/`multi` 且 `analyze_options` 判有选项 → **直接返回模型值，不做任何交叉校验**。

### 3.4 而评测脚本的噪声设计从不触碰这条轴

```python
NOISE_CANDIDATES = {
    "single_choice":  ["fill_in_blank", "detailed_answer"],   # 绝不注入 multi_choice
    "multi_choice":   ["fill_in_blank", "detailed_answer"],   # 绝不注入 single_choice
    "fill_in_blank":  ["choice", "choice", "detailed_answer"],# "choice" 是三值粗粒度
    "detailed_answer":["choice", "choice", "fill_in_blank"],
}
```

- 情形 A：假设模型四值全对 → 单选/多选这条轴**被假设对了**
- 情形 B：20% 噪声**从不注入错误的 single/multi**

→ **「单选/多选取分 24/24 = 100%」这个头条数字，是在"模型已经答对单选/多选"的前提下测出来的。这条轴从头到尾没有被任何一套题测过。**

### 判定

**三个集合的 100% 不是过拟合**（反作弊清白 + 换标签反证有效），**但也不是这条轴上的真实泛化能力证明** —— 它是"单选/多选这条轴被指标豁免"的必然结果。三集合同时 100%，恰恰因为这条轴上的答案全被指标自己喂进去了。

---

## 4. 生产风险实测：「模型判错 + 规则层证据弱」（`probe_silent.py` / `probe_ab_version.py`）

构造方式：`difficulty` 与 `chapter_code` 都给**正确且合法**的值（模拟"模型其余字段都对、唯独题型判错"的最理想生产环境），只把 `question_type` 换成错误四值。这样 `needs_review` 若为真，只能是题型/章节自己触发的。

### 4.1 三集合合计（162 条）

| 指标 | 返工前 | 返工后 |
| --- | --- | --- |
| 规则层成功纠正回真值 | — | 83/162 = 51.2% |
| 跟着模型一起错 | 17/162 = 10.5% | **79/162 = 48.8%** |
| **静默接受（错且 `needs_review=False`）** | 9/162 = 5.6% | **77/162 = 47.5%** |

分集合：

| 集合 | 返工前 跟错/静默 | 返工后 跟错/静默 | 变化 |
| --- | --- | --- | --- |
| cases | 0 / 0 | 31 / 31 | **+31 / +31** |
| holdout | 7 / 4 | 24 / 24 | **+17 / +20** |
| final | 10 / 5 | 24 / 22 | **+14 / +17** |

→ **这是本次返工明确引入的回归**：原来规则层会用多选提示语独立裁决单选/多选（代价是"无提示语多选题"会误判成单选，即我上轮报的 R4 那 9 条）；现在改成无条件采信模型，**把唯一的交叉校验一起删掉了**。修好了"规则层静默覆盖"，换来"模型静默错误"。

### 4.2 静默只发生在一条轴上

| 错误方向 | 静默条数 |
| --- | --- |
| 真单选 → 输出多选 | 39 |
| 真多选 → 输出单选 | 38 |
| 真填空 → 注入单选 | **0**（规则层全部纠正） |
| 真解答 → 注入多选 | **0**（规则层全部纠正） |

**「选择 vs 填空/解答」守得住，「单选 vs 多选」防御力 = 0。**

### 4.3 最严重子集：规则层握有强反对证据，仍然一言不发

真值多选、题干里**明写**「多选题」/「有多项符合题目要求」/「全部选对得满分、部分选对得部分分」/「选出所有满足条件的」，模型却说 `single_choice`：

> **29/29 条全部静默输出 `single_choice`，`review_reasons = []`，`needs_review = False`。**

这是完全可防的一类冲突（正则直接命中，置信度极高），被 `overridden` 判定漏掉了 —— 因为 `overridden` 只看 `type_source in ("structure","corrected","rule")`，而采信分支返回的是 `"ai"`/`"structure"` 且 `ai_expected == question_type`，永远不触发。

### 4.4 复核机制在主路径上基本休眠

理想 payload 下 `needs_review=False` 的比例：**cases 66/66、holdout 47/48、final 46/48**（合计 159/162）。

final 里那 2 条被标复核的，理由都是「难度判定存在分歧：模型判为难题，规则先验为基础题」，**与题型错误无关，纯属碰巧**。

---

## 5. 回归 / 边界 / 章节

### 5.1 回归 —— 全部通过

| 项 | 结果 |
| --- | --- |
| 冷启动 import 顺序（4 个方向全新解释器 + `-W error::SyntaxWarning`） | 4/4 PASS，无循环 import、无 SyntaxWarning |
| `normalize_ai_question_form` 三值语义 | 15/15（`paper_helper` / `word_export_helper` 依赖安全） |
| `normalize_ai_question_type` 四值语义 | 12/12 |
| `detect_choice_options` 返回 `bool` | 3/3 |
| `main.py` 手动 `is_fallback=True` | 0 处（R7b 已修） |
| 前端字段消费 | `import.js` 已接 `review_reasons` / `question_type_candidates` / `question_type_source` / `is_fallback` |

### 5.2 边界 —— 零异常

| 项 | 结果 |
| --- | --- |
| 12 种边界输入 × 4 种 payload（None / {} / 非法类型 / 超纲值）= 48 组合 | 0 异常、0 缺字段、14 个字段类型全对 |
| `review_reasons` 恒为 `list[str]` | 48/48 |
| `needs_review=True` 时理由必非空 | 48/48 |
| 162 用例 × 9 种 `question_type` 取值 = 1458 组合不变量 | 0 违规 |

### 5.3 章节 —— 收紧术语后召回**没有变好**

| 集合 | 版本 | top-1 | top-3 | top-8 |
| --- | --- | --- | --- | --- |
| cases | 返工前 | 27.3% | 45.5% | 72.7% |
| cases | 返工后 | 28.8% | 50.0% | 72.7% |
| holdout | 返工前 | 25.0% | 47.9% | **62.5%** |
| holdout | 返工后 | 25.0% | 47.9% | **58.3%（−4.2pp）** |
| final | 返工前 | 29.2% | 41.7% | 52.1% |
| final | 返工后 | 29.2% | 41.7% | 52.1%（**完全不变**） |

- 你报的三个数字我逐位复现，一个不差。
- 新增 60 个课标术语把"无术语命中的节点"从 58 降到 14（**覆盖面**变宽），但在未见过的题上**召回没有提升**：holdout top-8 反而掉了 4.2pp，final 三项完全不变。
- 章节这条线仍是 ~25–29% top-1 的弱能力，且每次规则命中都会通过 `chapter_source="rule"` 触发复核。

---

## 6. 小问题（不阻断，但建议顺手修）

1. **来源标注不准**：162 条里有 120 条标 `question_type_source="structure"`，其中 **79 条**的单选/多选实际来自模型（`decide_question_type` L503-505 返回 `ai_type` 却标 `"structure"`）。`import.js:1148` 会把这个显示成"结构标记"，对老师是误导。
2. **边界过于自信**：`emoji` / 纯数字标点 被判 `fill_in_blank`（`source=structure`）。虽然会因章节 fallback 被标复核，但题型给得太自信。
3. **`main.py` 两处 partial 出口不再置 `is_fallback=True`**，只靠 `status="partial"` + `message`。若前端哪天只盯 `is_fallback` 会漏提示（当前 `import.js:1601` 用 status/消息，看起来 OK，建议与 fe-programmer 确认）。
4. **工作区有未提交的 `static/index.html` + `static/js/import.js` 改动**（不是我改的）。若本轮验收边界是"不含 `static/`"，请知悉。

---

## 7. 一句话结论

**三集合 100% 不是过拟合（反作弊清白、换标签反证有效），但也不是泛化能力证明——因为"单选 vs 多选"这条轴在新代码里被无条件采信模型，而情形 A/B 的噪声设计从不注入错误的 single/multi，指标根本没测到它；实测注入错误四值后 77/162（47.5%）被静默接受且 `needs_review=False`，其中 29/29 是规则层手里握着明写「多选题」这类强反对证据却一言不发，这是本次返工引入的回归（返工前同口径仅 9/162）。**

**最大剩余风险**：单选/多选这条轴上系统已退化为"模型的传声筒"，且复核机制在模型字段合法的主路径上 159/162 不触发；只要模型在这一条轴上判错（这正是大模型在"无提示语多选题"上最常见的错误），错题会以 `needs_review=False` 直接入库，老师看不到任何提示。

### 建议修法（供参考，未改代码）

在 `decide_question_type` 的 `explicit_ai` 分支里增加冲突检测，而不是无条件 return：

- `multi_signals` 非空 且 模型判 `single_choice` → 返回 `multi_choice` + 复核理由「题干出现多选提示语，与模型判定的单选题冲突」
- `multi_signals` 为空 且 模型判 `multi_choice` → 保留模型值，但追加 `choice_ambiguous` 式复核理由（当前 `choice_ambiguous` 只覆盖 `not ai_explicit`，把最需要保护的情形排除在外了）
- 把 `overridden` 的触发条件从"只看 `type_source`"放宽为"规则证据与模型显式结论冲突"

这样既保住本次返工"不静默覆盖模型"的初衷，又把"单选/多选"这条唯一没有交叉校验的轴补上。
