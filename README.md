# 风格化项目 · Follow Notes

把每日群聊、交易记录和研究碎片，自动整理成可回看的笔记。

## 日常流程

1. **速记**：打开 <http://127.0.0.1:8765/note>，左边 Ctrl+Enter 按 `{source, text, important, media}` 写入数据库，不猜作者。回车只换行。右边从后端读取，点进去改、删、搬条目都会写回同一份数据。图片落到 `data/media/YYYY-MM-DD/`。笔记首页也可从「速记」进入。作者栏里 TradingPlan 与 Thought 固定置顶，并用不同样式标出。
2. **检查与整理**：日笺页查看分层、情绪与综合结论；也可继续粘贴大段原文走旧的整理入口。修改后即时重新生成规则结论，避免展示过期 AI 结论。
3. **补证据**：需要时手动运行 DeepSeek 联网核实、不联网逻辑展开，或粘贴外部材料。保存外调材料后会重新生成规则结论；规则模式不会把已保存材料声称为已经验证或吸收的证据。需要整合材料时可再次运行 AI 综合。
4. **回看与归档**：按日期浏览或检索原文/来源，下载 Markdown。显式归档会保存到 `data/archive/YYYY-MM-DD.md`；选择写入 Notion 才会调用 Notion，失败时仍保留完整本地文件。

## 安装与运行

需要 Python 3.10+，建议建立虚拟环境：

```powershell
cd D:\codex测试\desk-notes
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

浏览器打开 <http://127.0.0.1:8765/note> 进入速记，<http://127.0.0.1:8765> 进入笔记。`app.py` 使用 Uvicorn 在本机 8765 端口运行，并开启代码重载。已有进程占用此端口时，直接访问现有服务，勿启动第二个服务。静态文件更新后刷新页面。

数据存于 `data/desk.db`。备份时保留此数据库、`data/archive/` 以及 `data/media/`；不要把密钥提交到版本库。

## AI 与降级

默认投入会尝试 AI 整理，不再要求先手工点综合。每次导入最多一次模型分类与一次综合，多日期导入只有最新变更日期尝试 AI 综合，其余日期立即生成规则结论。模型输入有数量和长度限制（综合最多取最新 24 张卡片，长文本会截断），完整原文仍保存在本地，不能把模型输出理解为已逐字分析全部材料。它只分析提供的笔记，不会自动联网查行情；联网核实仍需手动触发。AI 不可用、超时或返回内容不符合要求时，系统保留规则分类与规则结论，并显示实际分析引擎或降级说明。规则结果是关键词辅助整理，并非事实核验或可靠的投资建议。AI 观点也需要结合原文核对。

当前 DeepSeek 适配器使用本机 `D:\API\deepseek\deepseek_web.py`，由该适配器读取本地配置/密钥（现有环境使用 `D:\主观工程Project\config.json`）。这不是 pip 依赖的一部分；其他机器没有该适配器也能使用规则模式。API 可传 `use_llm: false` 明确禁用 AI。健康接口仅报告配置状态，配置存在不代表远端调用成功。

Notion 为可选功能：在 `.env` 设置 `NOTION_TOKEN` 和 `NOTION_DATABASE_ID`，数据库 ID 也可来自 `data/settings.json` 的 `notion.database_id`。内部集成须有目标数据库写入权限。保留现有属性结构：标题、日期、状态、情绪、立场、宏观要点、行业要点、个股动作、风险。属性是摘要，完整 Markdown 正文分块分批写入，不再只保留前 1800 字。中途失败会报告正文可能部分写入；本地文件完整保留。重复归档到 Notion 可能创建新页面。

## API

- `GET /note`：速记页。左边 Ctrl+Enter 即存，右边从同一接口读取并回写。
- `GET /api/notes/YYYY-MM-DD`：当天结构化条目 `{id, source, text, important, media}` 与作者 roster。没有这一天时返回空列表，不 404。
- `POST /api/notes/YYYY-MM-DD/items`：写入一条 `{source, text, important}` 或批量 `{items:[...]}`。作者是字段，不走 `parse_notes`。不调用模型。
- `PUT /api/notes/YYYY-MM-DD/group`：`{source, items}` 整组替换（右栏编辑保存）。带 `id` 的更新，不带的新增，未出现的删除。
- `POST /api/notes/YYYY-MM-DD/media`：multipart 图片，挂到 `source` 名下，文件写入 `data/media/YYYY-MM-DD/`。
- `GET /api/notes/media/YYYY-MM-DD/{file}`：读取图片。
- `DELETE /api/notes/YYYY-MM-DD`：清空当天速记。
- `POST /api/ingest`：`{"text":"...","fallback_date":"2026-09-20","use_llm":true}`；默认自动分析，文本上限 200000 字符，空白输入无效。
- `GET /api/days`、`GET /api/days/YYYY-MM-DD`：历史与当天详情。
- `GET /api/search?q=关键词`：跨日期搜索卡片原文/来源，查询上限 200 字符，最多返回 50 条。
- `PATCH /api/snippets/{id}`：修改 `layer`、`mood`、`stance` 枚举并重新生成规则结论。
- `POST /api/snippets/{id}/expand`：`provider` 为 `deepseek_web`、`deepseek_reason` 或 `paste`；问题上限 4000 字符，粘贴证据上限 100000 字符。
- `POST /api/days/YYYY-MM-DD/conclude`：`{"use_llm":true}`；默认 AI 分析，失败回退规则。
- `GET /api/days/YYYY-MM-DD/markdown`：直接下载，不写归档、不调用 Notion。
- `POST /api/days/YYYY-MM-DD/archive`：`{"to_notion":false}`；默认仅本地归档。
- `GET /api/health`：安全的配置状态；`POST /api/seed` 可导入示例并自动整理，会修改数据库。

日期必须使用有效的 `YYYY-MM-DD`。无效日期返回 400，缺失日期/卡片返回 404，字段长度或枚举验证失败返回 422。

## 测试

```powershell
python -m pip install pytest
python -m pytest -q
```

测试应使用临时 SQLite 数据库、临时归档目录及模拟 AI/Notion 客户端，不依赖真实密钥，不消耗模型额度，不覆盖真实日笺。API 集成测试还需要 `httpx`（已在运行依赖中）。浏览器检查可使用独立安装的 Playwright；浏览现有日笺不会触发 AI，提交/归档测试应拦截 API 或使用隔离数据库。
