# cizhenyu_world_bank

World Bank Indicators API v2 MCP 服务器 + CLI（零依赖 Python，stdio JSON-RPC），遵循磁帧鱼 `cizhenyu.tool.json` 协议 v1。

无需 API Key，直接查询世界银行 45+ 数据库、近 16,000 项全球宏观经济、人口、贸易、能源、教育与可持续发展时间序列指标。

## 工具清单

| 工具名 | 说明 |
|--------|------|
| `wb_indicator_data` | 核心数据查询：按国家/地区、指标代码、时间范围或最近 N 期（`mrv`/`mrnev`）查询时间序列数据 |
| `wb_search_indicators` | 检索指标代码：按关键词、主题 ID 或数据源 ID（默认 WDI `source=2`）搜索可用指标 |
| `wb_indicator_info` | 获取指标元数据：定义说明（`source_note`）、数据来源机构、所属主题与数据库 |
| `wb_lookup_countries` | 查询国家/地区代码：按名称、ISO2/ISO3、地理区域、收入组或贷款类型筛选 |
| `wb_list_topics` | 列出世界银行全部数据主题（Topics）及其 ID |
| `wb_list_sources` | 列出世界银行全部数据库/数据源（Sources）及其 ID 与更新日期 |
| `wb_list_classifications` | 列出分类参考代码表（`region` / `incomeLevel` / `lendingType` / `languages`） |
| `wb_health_check` | 检查世界银行 API v2 网络连通性 |

## 运行方式

```bash
python server.py                 # 启动 stdio MCP 服务器
python server.py list            # 列出全部可用工具
python server.py check           # 连通性自检
```

## CLI 示例

查询中国与美国最近 5 年 GDP（现价美元）：

```bash
python server.py wb_indicator_data --json "{\"country\":\"CHN;USA\",\"indicator\":\"NY.GDP.MKTP.CD\",\"mrnev\":5,\"language\":\"zh\"}"
```

按关键词搜索 WDI 通胀相关指标：

```bash
python server.py wb_search_indicators --json "{\"query\":\"inflation\",\"limit\":10}"
```

查询东亚与太平洋地区的高收入经济体（中文名称）：

```bash
python server.py wb_lookup_countries --json "{\"region\":\"EAS\",\"income_level\":\"HIC\",\"language\":\"zh\"}"
```

## 协议自检

```bash
echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}} | python server.py
```
