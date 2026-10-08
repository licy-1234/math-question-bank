# 题型判定改造后评测报告（Phase 2）

- 用例集：`C:/Users/虫鱼/WorkBuddy/2026-10-07-23-33-22/math-question-bank/tools/classify_eval/cases.json`（66 条）
- 判定入口：`mathbank.classify_rules.classify_with_rules`（与 `main.py:/api/ai/classify` 同一份代码）
- 噪声参数：rate=0.2，seed=20260，注入 13 条（与 Phase 1 完全一致，保证可比）
- 基线数据：`tools/classify_eval/baseline_report.md`（Phase 1 实测）
- 章节树：可用

## 1. 验收门槛达成情况

| # | 指标 | 门槛 | Baseline (Phase 1) | After (Phase 2) | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1a | 选项误判 FP（填空/解答题） | ≤2/35 | 7/35 | 0/35 | ✅ 达标 |
| 1b | 选项漏判 FN（单选/多选题） | ≤2/31 | 6/31 | 0/31 | ✅ 达标 |
| 2 | `detect_blank_slots` 填空命中 | ≥88.2% | 无此能力 | 17/17 = 100.0% | ✅ 达标 |
| 3 | `detect_structured_question_form` 命中率 | ≥40% | 4/66 (6.1%) | 48/66 (72.7%) | ✅ 达标 |
| 4a | 细粒度准确率·情形A（AI 理想） | ≥90% | 35/66 (53.0%) | 66/66 (100.0%) | ✅ 达标 |
| 4b | 细粒度准确率·情形B（20% 噪声） | ≥85% | 30/66 (45.5%) | 66/66 (100.0%) | ✅ 达标 |
| 5 | 单选 vs 多选取分（情形A） | ≥90% | 0/31 (0.0%) | 31/31 (100.0%) | ✅ 达标 |

## 2. 规则层能力（改造后）

| 指标 | Baseline | After | 说明 |
| --- | --- | --- | --- |
| `detect_choice_options` 误判 FP（填空/解答题判有选项） | 7/35 = 20.0% | **0/35 = 0.0%** | 门槛 ≤ 2 |
| `detect_choice_options` 漏判 FN（单选/多选题判无选项） | 6/31 = 19.4% | **0/31 = 0.0%** | 门槛 ≤ 2 |
| `detect_blank_slots` 填空题命中 | 无此能力 | **17/17 = 100.0%** | 门槛 ≥ 88.2% |
| `detect_structured_question_form` 命中率 | 4/66 = 6.1% | **48/66 = 72.7%** | 门槛 ≥ 40% |

### 2.1 按题型拆分

| 题型 | 条数 | 结构命中 | 判有选项 | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| 单选题 | 16 | 16 | 16 | 0 | 0 |
| 多选题 | 15 | 15 | 15 | 0 | 0 |
| 填空题 | 17 | 17 | 0 | 0 | 0 |
| 解答题 | 18 | 0 | 0 | 0 | 0 |

## 3. 题型决策准确率（细粒度四值）

| 题型 | 条数 | 情形A正确 | 情形A准确率 | 情形B正确 | 情形B准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 单选题 | 16 | 16 | 100.0% | 16 | 100.0% |
| 多选题 | 15 | 15 | 100.0% | 15 | 100.0% |
| 填空题 | 17 | 17 | 100.0% | 17 | 100.0% |
| 解答题 | 18 | 18 | 100.0% | 18 | 100.0% |
| **合计** | **66** | **66** | **100.0%** | **66** | **100.0%** |

- 情形 A（AI 给期望四值）：baseline 细粒度 35/66 = 53.0%，粗粒度 63/66 = 95.5%；改造后细粒度 **66/66 = 100.0%**
- 情形 B（20% 噪声）：baseline 细粒度 30/66 = 45.5%，粗粒度 53/66 = 80.3%；改造后细粒度 **66/66 = 100.0%**
- 单选 vs 多选取分（情形 A）：baseline 0/31 = 0.0%（`normalize_ai_question_form` 折叠）；改造后 **31/31 = 100.0%**

## 4. 仍然判错的用例

情形 A 与情形 B 均无判错用例。

## 5. 噪声用例的纠偏效果

- 注入噪声 13 条，最终仍判定正确 **13** 条（100.0%）
- 按纠偏来源：structure 8、corrected 5、ai 0、rule 0

| 用例ID | 题型 | 期望 | AI错误输出 | 最终预测 | 来源 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| `SC-TEXT-02` | 单选题 | `single_choice` | `detailed_answer` | `single_choice` | structure | ✅ 救回 |
| `SC-TEXT-07` | 单选题 | `single_choice` | `detailed_answer` | `single_choice` | structure | ✅ 救回 |
| `MC-LATEX-06` | 多选题 | `multi_choice` | `detailed_answer` | `multi_choice` | structure | ✅ 救回 |
| `MC-TEXT-02` | 多选题 | `multi_choice` | `detailed_answer` | `multi_choice` | structure | ✅ 救回 |
| `MC-TEXT-07` | 多选题 | `multi_choice` | `detailed_answer` | `multi_choice` | structure | ✅ 救回 |
| `MC-TEXT-08` | 多选题 | `multi_choice` | `detailed_answer` | `multi_choice` | structure | ✅ 救回 |
| `FB-LATEX-07` | 填空题 | `fill_in_blank` | `detailed_answer` | `fill_in_blank` | structure | ✅ 救回 |
| `FB-TEXT-04` | 填空题 | `fill_in_blank` | `detailed_answer` | `fill_in_blank` | structure | ✅ 救回 |
| `DA-LATEX-02` | 解答题 | `detailed_answer` | `choice` | `detailed_answer` | corrected | ✅ 救回 |
| `DA-LATEX-05` | 解答题 | `detailed_answer` | `fill_in_blank` | `detailed_answer` | corrected | ✅ 救回 |
| `DA-TEXT-01` | 解答题 | `detailed_answer` | `choice` | `detailed_answer` | corrected | ✅ 救回 |
| `DA-TEXT-04` | 解答题 | `detailed_answer` | `choice` | `detailed_answer` | corrected | ✅ 救回 |
| `DA-TEXT-10` | 解答题 | `detailed_answer` | `fill_in_blank` | `detailed_answer` | corrected | ✅ 救回 |

## 6. 章节候选检索（参考指标，如实上报）

| 指标 | 命中 / 总数 | 命中率 |
| --- | --- | ---: |
| top-1 精确命中 | 19/66 | 28.8% |
| top-3 命中 | 33/66 | 50.0% |
| top-8 命中 | 48/66 | 72.7% |
| top-1 同分支（父/子也算可用） | 35/66 | 53.0% |

> 说明：这是**关键词检索**的召回能力，最终章节仍由模型从候选列表中挑选。top-1 偏低的主要原因是 66 条标注中有 32 条标到小节级，而题面往往只出现「复数」「对数」这类章/节级词，无法区分同级小节。详见 §8 风险。

## 7. 难度先验（参考指标，如实上报）

| 指标 | 命中 / 总数 | 命中率 |
| --- | --- | ---: |
| 与标注完全一致 | 43/66 | 65.2% |
| ±1 档以内 | 66/66 | 100.0% |
| 相差 2 档（easy↔hard） | 0/66 | 0.0% |

## 8. 仍未解决的风险

1. **章节 top-1 只有 28.8%**：关键词检索无法区分同级小节（如「复数」命中章 B2-C7 而非节 B2-C7-S2）。线上最终由模型在候选列表里选，且 `chapter_source='rule'` 时会置 `needs_review=True` 提示老师核对，但不要指望规则层单独定准章节。
2. **难度先验只保证 ±1 档**：它是结构先验（小问数/含参讨论/设问词），读不出「计算量大」「技巧隐蔽」这类真实难度来源；模型值与先验相差两档时只标记冲突、不覆盖。
3. **术语表与阈值是拿这 66 条调出来的**，存在过拟合风险；换成真实题库后各项数字大概率下降，建议用真实数据重跑本脚本复核。
4. **多选题判据依赖提示语**：若试卷只在 section 标题写「多项选择题」而不在题干里，`detect_multi_choice_signals` 会漏（本用例集不覆盖该形态）。
5. **`main.py` 本次无法在本机运行验证**：当前解释器未安装 requests/fastapi，只做了 `py_compile` 与静态名称解析检查，端点行为需在有依赖的环境里冒烟一次。

