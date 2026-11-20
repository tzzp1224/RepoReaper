# SWE-QA-Pro 固定子集 · 自定义 RepoReaper Agent

这是离线评测入口，复用产品的选择性建索引、混合检索和 JIT；不使用在线 auto-eval 分数。
不属于原论文固定 Agent 配置，也不构成官方榜单排名。主指标是五维总分 5–50，不能称为准确率。

## 环境与数据

从项目根目录使用已安装 requirements.txt 的 Python 环境。建议 Python 3.11；现有 `.venv` 也可用。
Qdrant 强制采用每个子进程独立的本地目录，不连接生产数据库。不会执行目标仓库代码。

```bash
python -m evaluation.benchmark preflight
python -m evaluation.benchmark fetch --output evaluation/benchmark_data/sweqa
python -m evaluation.benchmark prepare --data evaluation/benchmark_data/sweqa --run evaluation/benchmark_runs/interview
python -m evaluation.benchmark clone --run evaluation/benchmark_runs/interview --repos evaluation/benchmark_repos
```

需要本地 `.env` 的底座模型凭据、`SILICON_API_KEY` 和独立评分的 `OPENAI_API_KEY`。
当前计量适配支持 DeepSeek/OpenAI 兼容底座，评分固定 `gpt-5-2025-08-07`。
凭据不进入冻结配置、请求清单或结果；不要把 `.env` 发到聊天、提交到 Git。

fetch 固定 Hugging Face 数据集 revision 和官方评分提示词 Git commit、SHA256。
prepare 每仓库按种子 42 的 SHA256 排序选 1 题开发、4 题候选正式样本；标记一题用于证据标注和重复运行。
生成清单只有问题与仓库元数据，参考答案写入独立 `references.json`，不传入生成子进程。
问题生成与评分通过不同的应用接口运行。仓库工具只读 Git 对象与已索引数据，评测期间不执行目标仓库的代码。

## 费用与运行

已提供 `rates.deepseek-v4-flash.2026-09-07.json`：按核验日的峰时/未命中缓存价格、假设汇率 7.5 CNY/USD
计算保守预算，BGE-M3 使用官方免费档。运行前复核价格与账单币种；这不是实时汇率报价。
生产默认模型为 `deepseek-flash`。上述价格文件对应历史的 `deepseek-v4-flash` 实验，现有别名可能由供应商路由到新模型。
新实验使用 `--model` 指定模型或沿用环境配置，复制 `rates.example.json` 并按实际供应商填写费率。
模型和配置记录在冻结清单里，结果应说明使用的模型。
美元价格换算应记录汇率假设。空价格拒绝运行；不要把未知价格填成零。
价格用于预算预留和 Token 费用估算，不代替供应商账单。缓存折扣未使用时，估算会偏保守。

```bash
python -m evaluation.benchmark run --run evaluation/benchmark_runs/interview --repos evaluation/benchmark_repos --rates /absolute/path/rates.json --split dev
python -m evaluation.benchmark judge --run evaluation/benchmark_runs/interview --split dev
python -m evaluation.benchmark report --run evaluation/benchmark_runs/interview --split dev
python -m evaluation.benchmark select --run evaluation/benchmark_runs/interview
python -m evaluation.benchmark run --run evaluation/benchmark_runs/interview --repos evaluation/benchmark_repos --rates /absolute/path/rates.json --split test
python -m evaluation.benchmark run --run evaluation/benchmark_runs/interview --repos evaluation/benchmark_repos --rates /absolute/path/rates.json --split test --repeats
python -m evaluation.benchmark judge --run evaluation/benchmark_runs/interview --split test
python -m evaluation.benchmark report --run evaluation/benchmark_runs/interview --split test
```

四组：A 不读仓库；B 静态 RAG，JIT=0；C 完整 Agent；D 仅关闭查询重写。
每次建索引不接触问题；种子索引关闭后校验封存，每题复制到独立工作目录。
每题进程独立，B/C/D 的初始索引和摘要一致，结束后的 JIT 状态不会回写种子。
题目结束后删除它的临时数据库副本，保留原始回答、逐轮证据与工具轨迹；封存种子保持不变。
正式阶段前 `select` 根据完整开发集及评分的实际计量费用估算，含 50% 余量及重复实验，选择每仓库 4 或 2 题。
两种规模均超预算时停止。不会按成绩选择样本。开发集失败或缺独立评分时不放行正式实验。

总计量上限默认 **450 元**，为约定的 500 元保留 50 元。每个真实 SDK 尝试先用 SQLite 事务预留
输入字节数及输出上限对应费用，再用返回 usage 结算。取消、断流、未知 usage 留存预留金额，不算零费用。
SDK 隐藏重试关闭，产品重试仍逐次计量。生成过程中预算耗尽时记录失败；不要另建目录绕过预算重新运行。
新实验目录使用独立账本，重开实验时必须从 450 元中扣除此前已消费费用并设置 `--budget`。

运行固化模型、配置、源码哈希、项目 commit、价格及清单。源码或配置变化后拒绝混用结果。
不要修改正式运行中的源码。开发阶段必须调整时，启动新实验并扣除已花费用。
已写入 `record.json` 的题目不会重复；中断且无结果的题目记录 interrupted。
独立评分失败可以用 `judge --retry-failed` 重试，追加真实费用。生成失败保留原始终态，不择优重跑。
子进程日志可能包含仓库源码/回答，发布前检查；它们默认 gitignored。

## 人工证据核验与交付

在查看正式输出前填写 `evidence_labels.json`：根据对应 Git 快照标注必要文件、标注人，设置
`verified_before_review=true`。这是自建补充标签，不是官方标签。现有分块含合成上下文，行号仅作提示，
证据检查按原始文件进行，不以未经核验的行号作为精确引用。

报告目录包含 `summary.json`、`results.csv`、`report.md`、`quality_cost.svg` 和匿名 `blind_review.json`。
将 `review_key.json` 与盲审问卷分开保管。先评关键结论正确性/代码支持情况，再揭盲分析工具轨迹。
failure_category 使用 `index_coverage`、`retrieval_miss`、`truncation`、`path_error`、`reasoning_error` 或 `none`。
疑似参考答案错误写入 notes，原始参考答案保持不变。

主比较 C-B、次比较 C-D；同题配对差值按仓库 bootstrap 2000 次给出 95% 区间。
一题/仓库的 B/C 各有两个额外重复，单列三次分数范围，不混入主均分。
报告列出状态分母、评分覆盖率与按阶段费用。缺生成终态或非空答案未评分时，只输出进度表，不输出正式比较。
延迟是单题端到端耗时；建索引/报告生成成本单独统计，供应商请求/重试费用全部留存在 `usage.sqlite`。
独立评分分数只是参考答案驱动的 LLM 判断，应结合人工证据核验解释。
完成标签/盲审后再次运行 `report`，会增加 `evidence_metrics.json` 和 `failure_types.svg`，不会覆盖人工问卷。
其中 JIT 必要文件命中率只是必要证据覆盖的代理指标；文件被读到不代表推理一定使用正确。
均摊费用按 B/C/D 各自作为独立系统承担整个种子构建成本计算，A 不承担种子成本；实际实验总账只收取一次构建。

本仓库的 63 题黄金集用于开发回归。跨仓库结论应使用单独留出的样本，并同时报告 JIT 有效、无效和答案错误的案例。

来源：[数据集](https://huggingface.co/datasets/TIGER-Lab/SWE-QA-Pro-Bench)、
[官方评测](https://github.com/TIGER-AI-Lab/SWE-QA-Pro/tree/main/eval)。

## C/F/G JIT 对照试跑

- `C` 跑旧多轮 JIT。`F` 跑新版原始源码片段流程。`G` 使用与 `F` 相同的流程，在存在多个候选批次时允许 Jev 改选。
- 三组从同一个已封存的旧索引复制起点。`F` 和 `G` 使用它提供初始检索线索，再回到同一 Git SHA 读取原始片段。这个对照测量问答链路，生产版的增量建库需单独评估。
- `prepare-jit` 在每个指定仓库选一条未出现在旧清单中的题目。先在 `evidence_labels.json` 标注必要文件，再运行生成。评分器只接收测试题、参考答案和候选答案；TypeSafe 只接收问题和候选路径。组顺序按题轮换。
- `G` 使用进程内的 `active`，用户 `.env` 的模式与生产超时保持原样。Jev 的调用、时限、获选批次和 token 单独记录，并使用同一个费用账本。`--rates` 增加 `jev` 费率项，格式与 `generation` 相同。

```bash
python -m evaluation.benchmark prepare-jit --data evaluation/benchmark_data/sweqa \
  --run evaluation/benchmark_runs/jit-pilot --seed-run evaluation/benchmark_runs/interview \
  --repo mkdocs/mkdocs --repo getsentry/responses
python -m evaluation.benchmark run --run evaluation/benchmark_runs/jit-pilot \
  --repos evaluation/benchmark_repos --seed-run evaluation/benchmark_runs/interview \
  --rates /absolute/path/rates.json --split dev --budget 10 \
  --model deepseek-flash --jev-model jev-1.14.0 --jev-timeout 1.5
python -m evaluation.benchmark judge --run evaluation/benchmark_runs/jit-pilot --split dev
python -m evaluation.benchmark report --run evaluation/benchmark_runs/jit-pilot --split dev
```

报告同时保留各组的失败分母、最终答案首字时间、端到端时延、token 用量、证据文件覆盖和 Jev 改选次数。开发试跑的结果用于决定后续样本和策略。小样本 p95 仅作描述。

`prepare-jit --experiment exploration --exclude-run <previous-pilot>` 生成 F/H/I 对照。F 使用旧路径排序，H 使用组件词拆分及去仓库名的路径排序，I 在 H 的基础上允许模型进行有界工具探索。这三组禁用 Jev，使用同一封存索引、问题快照和评分规则。可多次传递 `--exclude-run` 排除以往清单。

```bash
python -m evaluation.benchmark prepare-jit --experiment exploration \
  --data evaluation/benchmark_data/sweqa --seed-run evaluation/benchmark_runs/interview \
  --exclude-run evaluation/benchmark_runs/jit-pilot --run evaluation/benchmark_runs/jit-exploration \
  --repo mkdocs/mkdocs --repo getsentry/responses
python -m evaluation.benchmark run --run evaluation/benchmark_runs/jit-exploration \
  --repos evaluation/benchmark_repos --seed-run evaluation/benchmark_runs/interview \
  --rates /absolute/path/rates.json --model deepseek-flash --split dev --budget 10
python -m evaluation.benchmark judge --run evaluation/benchmark_runs/jit-exploration --split dev
python -m evaluation.benchmark report --run evaluation/benchmark_runs/jit-exploration --split dev
```

`--experiment source` 生成 H/K/L 对照。H 使用前一轮的组件词排序，K 增加实现路径优先级、CI 配置优先级和三个候选文件的读取批次，L 在 K 上打开模型探索。使用 `--exclude-run` 排除前两轮试跑，保持同一个种子来源和评分器。

`--experiment tools` 生成 K/L/M 对照。K 使用规则召回，L 使用相同的首轮规则召回并允许 LLM 补充探索，M 从首轮开始由 Jev 选择工具和候选参数。M 使用配置在 `--jev-model` 的模型，运行命令加 `--jev-timeout 1.5` 可复现实验超时。报告单列实际执行的 Jev 动作、决策耗时和规划 token，以及端到端质量与时延。L 的非流式请求含查询重写（若触发）和工具规划。
