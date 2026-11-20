# World Bank Data 配置说明

## 无需密钥，开箱即用

世界银行 Indicators API v2 **完全开放、无需 API Key 或注册账号**，安装启用后即可直接调用。

- 官方文档：[World Bank Indicators API Documentation](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation)
- 接口基址：`https://api.worldbank.org/v2/`
- 覆盖范围：45+ 数据库、近 16,000 项宏观与行业时间序列指标，部分数据可追溯 50 年以上，支持中英文等多语言名称。

## 连通性验证

- 工具箱启用后，可让 Agent 调用 `wb_health_check` 检查网络连通性与最新数据更新时间。
- 命令行自检：

```bash
python server.py check
```

## 常用代码速查

### 1. 常用国家 / 地区代码（ISO3 或 ISO2，多国用 `;` 分隔）

- `CHN` / `CN`：中国
- `USA` / `US`：美国
- `JPN` / `JP`：日本
- `DEU` / `DE`：德国
- `GBR` / `GB`：英国
- `IND` / `IN`：印度
- `WLD`：全球合计
- `EAS`：东亚与太平洋地区
- `all`：全部国家与聚合地区

可使用 `wb_lookup_countries` 按名称、地区、收入组或贷款类型查询任意国家代码。

### 2. 常用 WDI 指标代码（`source=2`）

| 指标代码 | 说明 |
|----------|------|
| `NY.GDP.MKTP.CD` | GDP（现价美元） |
| `NY.GDP.MKTP.KD.ZG` | GDP 实际增长率（年百分比） |
| `NY.GDP.PCAP.CD` | 人均 GDP（现价美元） |
| `SP.POP.TOTL` | 总人口 |
| `FP.CPI.TOTL.ZG` | 消费者物价通胀率（年百分比） |
| `NE.TRD.GNFS.ZS` | 贸易占 GDP 百分比 |
| `BX.KLT.DINV.CD.WD` | 外国直接投资净流入（现价美元） |
| `SL.UEM.TOTL.ZS` | 失业率（占总劳动力百分比） |
| `IT.NET.USER.ZS` | 互联网普及率（占人口百分比） |

可使用 `wb_search_indicators` 按关键词、主题（`wb_list_topics`）或数据库（`wb_list_sources`）检索更多指标代码。

### 3. 日期与筛选参数提示

- `date`：单年 `2023`、年份区间 `2015:2024`、月度 `2023M01:2023M12`、季度 `2023Q1:2023Q4`、年初至今 `YTD:2024`。
- `mrv` / `mrnev`：获取最近 N 期值 / 最近 N 期非空值（适合快速获取最新数据，无需猜测最新年份）。
- 多指标同时查询（如 `NY.GDP.MKTP.CD;SP.POP.TOTL`）时需指定同一 `source`（未指定时默认 `2`，即 WDI）。
