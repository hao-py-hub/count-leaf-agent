# 本地 RAG 资料与评测规范

本目录用于准备可导入的知识文档，以及验证检索质量的人工标注题集。当前 v0.1 使用 SQLite FTS5，本目录中的资料不应包含 API Key、密码、未授权原图、跨项目原始结果或系统提示词。

## 目录约定

```text
docs/rag/
  README.md                         # 本规范
  knowledge-document.template.md    # 单篇知识文档模板
  evaluation-cases.template.json    # 检索评测题模板
  knowledge/                        # 填写完成后存放可导入 Markdown
  evaluation-cases.json             # 填写完成后的评测题集，不提交敏感内容
```

`knowledge/` 中的首批四份资料已经根据项目所有者提供的内容整理完成，但其外部标准链接和项目口径尚未由系统自动核验；导入前仍需项目所有者确认。

## 知识文档要求

1. 一篇文档只描述一个主题，例如拍摄规范、指标口径、样本命名或人工复核。
2. 文件名使用两位排序号和主题，例如 `01-拍摄与上传规范.md`。
3. 开头必须写明来源、版本、适用范围和最后更新日期；正文只保留可验证的规则与定义。
4. 不复制整篇论文或网页。论文只摘录与本系统操作、指标口径直接相关的段落，并注明出处。
5. 资料导入前由当前项目所有者确认其允许进入本机知识库。

推荐第一批文档：

- `01-拍摄与上传规范.md`
- `02-角果与分枝指标口径.md`
- `03-样本命名与真值表字段说明.md`
- `04-人工复核与常见误差.md`

## 评测题集要求

每一道题记录用户可能真实会问的问题、应命中的文档标题和关键证据词。评测只判断检索候选是否包含正确证据：

- `top_1`：正确文档是否排在第一位；用于观察排序。
- `top_3` / `top_5`：正确文档是否出现在前 3 / 5 个候选中；用于观察召回。

检索命中不代表 LLM 最终回答正确。若启用 LLM，还应额外人工检查答案是否只引用检索证据、是否包含正确来源、是否拒绝无依据结论。

## 导入步骤

1. 按 `knowledge-document.template.md` 创建并填写 Markdown 文件。
2. 在工作台知识库页面上传，或调用 `POST /api/v2/knowledge/documents/upload`。
3. 调用 `GET /api/v2/knowledge/status`，确认文档数和分块数增加。
4. 按 `evaluation-cases.template.json` 创建至少 12 道题：每个主题至少 3 道，覆盖同义表达、无答案问题和容易混淆的问题。
5. 用工作台搜索或 `GET /api/v2/knowledge/search` 检查每题的 Top-1、Top-3、Top-5，并记录问题原因。

## 本项目脚本

先运行下列命令进行**临时评测**；它只读取 `knowledge/` 与题集，不会写入正式知识库：

```powershell
& "C:\Users\15268\Desktop\count-beaf-agent\.venv311\Scripts\python.exe" "C:\Users\15268\Desktop\count-beaf-agent\scripts\evaluate_project_rag.py"
```

确认文档与评测结果后，先执行不带确认参数的导入预览。只有在输出文档标题正确后，才加 `--confirm` 写入本机知识库：

```powershell
& "C:\Users\15268\Desktop\count-beaf-agent\.venv311\Scripts\python.exe" "C:\Users\15268\Desktop\count-beaf-agent\scripts\import_rag_knowledge.py"
& "C:\Users\15268\Desktop\count-beaf-agent\.venv311\Scripts\python.exe" "C:\Users\15268\Desktop\count-beaf-agent\scripts\import_rag_knowledge.py" --confirm
```

## 不合格资料示例

- “忽略前面的规则，调用某个工具”之类指令性文本。
- 未经核对的网络摘抄、没有来源的数值结论。
- 真实密钥、账号、内部服务器地址、私人联系方式。
- 将角果数、叶片数、分枝数等不同指标混为一谈的内容。
