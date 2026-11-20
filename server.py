#!/usr/bin/env python3
"""World Bank Indicators API v2 MCP 服务器 + CLI（零依赖，stdio JSON-RPC）。

查询世界银行开放数据（45+ 数据库、近 16,000 项时间序列指标）：
全球国家/地区的 GDP、人口、通胀、贸易、能源、教育、贫困等宏观指标数据，
无需 API Key 或注册账号。

官方文档：
  https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation
接口基址：
  https://api.worldbank.org/v2/

MCP 用法（磁帧鱼 Deploy / Claude / Cursor 挂载）：
  command: <python.exe>
  args:    ["server.py"]
  cwd:     <world-bank 目录>

CLI 用法（不依赖 MCP 也能直接调用）：
  python server.py list
  python server.py check
  python server.py wb_indicator_data --json "{\"country\":\"CHN;USA\",\"indicator\":\"NY.GDP.MKTP.CD\",\"mrnev\":5}"
  python server.py wb_search_indicators --json "{\"query\":\"GDP per capita\",\"limit\":10}"
  python server.py wb_lookup_countries --json "{\"query\":\"china\",\"language\":\"zh\"}"

协议自检：
  echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}} | python server.py
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

SERVER_NAME = "world-bank"
SERVER_VERSION = "1.0.0"

API_BASE = "https://api.worldbank.org/v2"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_INDICATORS_PER_QUERY = 60
MAX_PER_PAGE = 5000
DEFAULT_WDI_SOURCE_ID = "2"

# 日志强制输出到 stderr，避免污染 MCP stdio JSON-RPC 的 stdout 通道
logging.basicConfig(
    level=logging.WARNING,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(SERVER_NAME)

# 合法路径片段校验，防止恶意路径穿越或无效字符注入 URL
_SAFE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9._;\-]+$")
_SAFE_LANG_PATTERN = re.compile(r"^[A-Za-z]{2,5}(?:-[A-Za-z]{2,4})?$")


class WorldBankError(RuntimeError):
    """世界银行 API 调用或响应解析异常。"""


# ------------------------------------------------------------------ HTTP 与解析

def _int_or(value: Any, default: int) -> int:
    """将 API 返回的可能是字符串的数字字段安全转换为整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or_none(value: Any) -> float | None:
    """将经纬度等字符串数值安全转换为浮点数。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_path_segment(value: str, field_name: str) -> str:
    """校验并规范化 URL 路径片段，避免非法字符导致请求异常。"""
    cleaned = value.strip()
    if not cleaned:
        raise WorldBankError(f"参数 {field_name} 不能为空。")
    if not _SAFE_CODE_PATTERN.match(cleaned):
        raise WorldBankError(
            f"参数 {field_name} 包含非法字符：{cleaned!r}（仅允许字母、数字、点、下划线、连字符与分号）。"
        )
    return cleaned


def _normalize_language(language: str | None) -> str:
    """校验语言代码，空值返回空串（API 默认使用英文）。"""
    if not language:
        return ""
    cleaned = language.strip().lower()
    if not cleaned or cleaned == "en":
        return ""
    if not _SAFE_LANG_PATTERN.match(cleaned):
        raise WorldBankError(f"无效的语言代码：{language!r}（如 en、zh、es、fr、ar、ja）。")
    return cleaned


def _request_v2(
    path: str,
    params: dict[str, Any] | None = None,
    language: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """调用世界银行 V2 JSON 接口，统一解析分页元信息、数据列表与业务错误。"""
    lang = _normalize_language(language)
    clean_path = path.lstrip("/")
    prefix = f"{API_BASE}/{lang}" if lang else API_BASE

    query_params: dict[str, Any] = {"format": "json"}
    if params:
        for key, val in params.items():
            if val is not None and val != "":
                query_params[key] = val

    url = f"{prefix}/{clean_path}?{urllib.parse.urlencode(query_params)}"
    headers = {
        "Accept": "application/json",
        "User-Agent": f"cizhenyu/{SERVER_NAME}/{SERVER_VERSION}",
    }

    logger.debug("Requesting World Bank API: %s", url)
    req = urllib.request.Request(url, headers=headers)
    raw = ""
    last_err: Exception | None = None
    # 世界银行公共 API 偶发慢响应或 5xx，对瞬时网络/超时错误自动重试 1 次
    for attempt in range(2):
        try:
            with urllib.request.urlopen(
                req,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            ) as resp:
                raw = resp.read().decode("utf-8", "replace")
                last_err = None
                break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code >= 500 and attempt == 0:
                logger.warning("World Bank API HTTP %d, retrying once...", exc.code)
                last_err = WorldBankError(f"HTTP {exc.code}: {detail}")
                continue
            raise WorldBankError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 0:
                logger.warning("World Bank API network/timeout error (%s), retrying once...", exc)
                last_err = WorldBankError(f"网络或超时错误: {exc}")
                continue
            raise WorldBankError(f"网络或超时错误: {exc}") from exc

    if last_err is not None:
        raise last_err

    try:
        payload = json.loads(raw) if raw else []
    except ValueError as exc:
        raise WorldBankError(f"响应非有效 JSON: {raw[:200]}") from exc

    # 世界银行 V2 API 业务错误时返回单元素列表：[{"message": [{"id": ..., "key": ..., "value": ...}]}]
    if isinstance(payload, list) and len(payload) >= 1 and isinstance(payload[0], dict):
        if "message" in payload[0]:
            messages = payload[0].get("message") or []
            if isinstance(messages, list) and messages:
                parts = [
                    f"[{m.get('id', '')}] {m.get('key', '')}: {m.get('value', '')}".strip()
                    for m in messages
                    if isinstance(m, dict)
                ]
                raise WorldBankError("API 返回错误：" + " ; ".join(parts))
            raise WorldBankError(f"API 返回错误：{payload[0]['message']}")

        meta = payload[0]
        items = payload[1] if len(payload) >= 2 and isinstance(payload[1], list) else []
        return meta, items

    raise WorldBankError(f"响应格式异常: {str(payload)[:200]}")


def _format_pagination(meta: dict[str, Any]) -> dict[str, Any]:
    """统一提取分页与更新元信息。"""
    out: dict[str, Any] = {
        "page": _int_or(meta.get("page"), 1),
        "pages": _int_or(meta.get("pages"), 1),
        "per_page": _int_or(meta.get("per_page"), 50),
        "total": _int_or(meta.get("total"), 0),
    }
    if meta.get("sourceid"):
        out["source_id"] = str(meta["sourceid"])
    if meta.get("lastupdated"):
        out["last_updated"] = str(meta["lastupdated"])
    return out


# ------------------------------------------------------------------ 内存缓存

_countries_cache: dict[str, list[dict[str, Any]]] = {}
_indicators_cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}


def _fetch_all_countries(language: str | None = None) -> list[dict[str, Any]]:
    """拉取并缓存全部国家与聚合地区列表（全量约 300 条，单次请求即可取完）。"""
    lang_key = _normalize_language(language) or "en"
    if lang_key in _countries_cache:
        return _countries_cache[lang_key]
    _, items = _request_v2("country", {"per_page": 500}, language=language)
    _countries_cache[lang_key] = items
    return items


def _fetch_indicators_pool(
    source_id: str | None = None,
    topic_id: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """按数据源或主题拉取并缓存指标列表，用于本地关键词检索。"""
    lang_key = _normalize_language(language) or "en"
    src_key = (source_id or "").strip()
    top_key = (topic_id or "").strip()
    cache_key = (lang_key, src_key, top_key)
    if cache_key in _indicators_cache:
        return _indicators_cache[cache_key]

    if top_key:
        safe_topic = _validate_path_segment(top_key, "topic_id")
        path = f"topic/{safe_topic}/indicator"
    else:
        safe_source = _validate_path_segment(src_key or DEFAULT_WDI_SOURCE_ID, "source_id")
        path = f"source/{safe_source}/indicator"

    # 分页拉取该数据源/主题下的指标，最多拉取 5 页（单页 2000 条，覆盖绝大多数数据库）
    all_items: list[dict[str, Any]] = []
    page = 1
    while page <= 5:
        meta, items = _request_v2(path, {"per_page": 2000, "page": page}, language=language)
        all_items.extend(items)
        total_pages = _int_or(meta.get("pages"), 1)
        if page >= total_pages:
            break
        page += 1

    _indicators_cache[cache_key] = all_items
    return all_items


# ------------------------------------------------------------------ 业务工具函数

def indicator_data(
    country: str,
    indicator: str,
    date: str | None = None,
    mrv: int | None = None,
    mrnev: int | None = None,
    gapfill: str | None = None,
    frequency: str | None = None,
    source: str | None = None,
    footnote: bool = False,
    language: str = "en",
    page: int = 1,
    per_page: int = 100,
    exclude_nulls: bool = False,
) -> dict[str, Any]:
    """查询世界银行指标时间序列数据。"""
    safe_country = _validate_path_segment(country, "country")
    safe_indicator = _validate_path_segment(indicator, "indicator")

    indicator_list = [x for x in safe_indicator.split(";") if x]
    if len(indicator_list) > MAX_INDICATORS_PER_QUERY:
        raise WorldBankError(f"单次最多查询 {MAX_INDICATORS_PER_QUERY} 个指标，当前传入 {len(indicator_list)} 个。")

    # 世界银行 V2 API 要求多指标查询必须指定同一 source，未提供时默认使用 WDI（source=2）
    effective_source = source.strip() if isinstance(source, str) and source.strip() else None
    if len(indicator_list) > 1 and not effective_source:
        effective_source = DEFAULT_WDI_SOURCE_ID

    capped_per_page = max(1, min(_int_or(per_page, 100), MAX_PER_PAGE))
    safe_page = max(1, _int_or(page, 1))

    params: dict[str, Any] = {
        "page": safe_page,
        "per_page": capped_per_page,
    }
    if date:
        params["date"] = date.strip()
    if mrv is not None:
        params["mrv"] = max(1, _int_or(mrv, 1))
    if mrnev is not None:
        params["mrnev"] = max(1, _int_or(mrnev, 1))
    if gapfill:
        gf = gapfill.strip().upper()
        if gf not in ("Y", "N"):
            raise WorldBankError("gapfill 仅支持 'Y' 或 'N'。")
        params["gapfill"] = gf
    if frequency:
        freq = frequency.strip().upper()
        if freq not in ("Y", "Q", "M"):
            raise WorldBankError("frequency 仅支持 'Y'（年）、'Q'（季）或 'M'（月）。")
        params["frequency"] = freq
    if effective_source:
        params["source"] = effective_source
    if footnote:
        params["footnote"] = "y"

    path = f"country/{safe_country}/indicator/{safe_indicator}"
    meta, items = _request_v2(path, params, language=language)

    records: list[dict[str, Any]] = []
    non_null_count = 0
    for item in items:
        val = item.get("value")
        if val is None and exclude_nulls:
            continue
        if val is not None:
            non_null_count += 1
        ind_obj = item.get("indicator") or {}
        cty_obj = item.get("country") or {}
        rec: dict[str, Any] = {
            "country_code": cty_obj.get("id") or "",
            "country_iso3": item.get("countryiso3code") or "",
            "country": cty_obj.get("value") or "",
            "indicator_code": ind_obj.get("id") or "",
            "indicator": ind_obj.get("value") or "",
            "date": item.get("date") or "",
            "value": val,
            "unit": item.get("unit") or "",
            "obs_status": item.get("obs_status") or "",
            "decimal": item.get("decimal"),
        }
        if footnote and item.get("footnote"):
            rec["footnote"] = item.get("footnote")
        records.append(rec)

    return {
        "query": {
            "country": safe_country,
            "indicator": safe_indicator,
            "date": date,
            "mrv": mrv,
            "mrnev": mrnev,
            "gapfill": gapfill,
            "frequency": frequency,
            "source": effective_source,
            "language": language,
        },
        "pagination": _format_pagination(meta),
        "returned": len(records),
        "non_null_count": non_null_count,
        "records": records,
    }


def _clean_indicator_meta(item: dict[str, Any]) -> dict[str, Any]:
    """清洗单条指标元数据字段。"""
    source_obj = item.get("source") or {}
    topics_raw = item.get("topics") or []
    topics = [
        {"id": str(t.get("id", "")).strip(), "name": (t.get("value") or "").strip()}
        for t in topics_raw
        if isinstance(t, dict) and (t.get("id") or t.get("value"))
    ]
    return {
        "id": item.get("id") or "",
        "name": item.get("name") or "",
        "unit": item.get("unit") or "",
        "source_id": str(source_obj.get("id", "")),
        "source_name": source_obj.get("value") or "",
        "source_note": item.get("sourceNote") or "",
        "source_organization": item.get("sourceOrganization") or "",
        "topics": topics,
    }


def search_indicators(
    query: str = "",
    source_id: str | None = None,
    topic_id: str | None = None,
    language: str = "en",
    limit: int = 25,
) -> dict[str, Any]:
    """按关键词、数据源或主题检索世界银行指标代码。"""
    pool = _fetch_indicators_pool(source_id=source_id, topic_id=topic_id, language=language)
    q = (query or "").strip().lower()
    tokens = [t for t in q.split() if t]

    matched: list[tuple[int, dict[str, Any]]] = []
    for item in pool:
        code = (item.get("id") or "").strip()
        name = (item.get("name") or "").strip()
        note = (item.get("sourceNote") or "").strip()
        haystack = f"{code} {name} {note}".lower()

        if not tokens:
            score = 0
        elif all(tok in haystack for tok in tokens):
            # 优先匹配代码精确命中与名称包含完整关键词的结果
            if q == code.lower():
                score = 0
            elif q in name.lower():
                score = 1
            elif all(tok in name.lower() for tok in tokens):
                score = 2
            else:
                score = 3
        else:
            continue

        matched.append((score, item))

    matched.sort(key=lambda pair: (pair[0], (pair[1].get("id") or "")))
    capped_limit = max(1, min(_int_or(limit, 25), 200))
    results = [_clean_indicator_meta(item) for _, item in matched[:capped_limit]]

    return {
        "query": query,
        "scope": {
            "source_id": source_id or (None if topic_id else DEFAULT_WDI_SOURCE_ID),
            "topic_id": topic_id,
            "language": language,
            "pool_size": len(pool),
        },
        "matched_count": len(matched),
        "returned": len(results),
        "indicators": results,
    }


def indicator_info(
    indicator: str,
    source: str | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """获取指定指标代码的完整定义、口径说明与数据来源。"""
    safe_indicator = _validate_path_segment(indicator, "indicator")
    params: dict[str, Any] = {}
    if source and source.strip():
        params["source"] = source.strip()

    meta, items = _request_v2(f"indicator/{safe_indicator}", params, language=language)
    return {
        "pagination": _format_pagination(meta),
        "indicators": [_clean_indicator_meta(item) for item in items],
    }


def lookup_countries(
    query: str = "",
    region: str | None = None,
    income_level: str | None = None,
    lending_type: str | None = None,
    include_aggregates: bool = True,
    language: str = "en",
    limit: int = 50,
) -> dict[str, Any]:
    """按名称、ISO 代码、区域、收入组或贷款类型查询国家/地区列表。"""
    items = _fetch_all_countries(language=language)
    q = (query or "").strip().lower()
    region_filter = (region or "").strip().upper()
    income_filter = (income_level or "").strip().upper()
    lending_filter = (lending_type or "").strip().upper()

    out: list[dict[str, Any]] = []
    for item in items:
        code = (item.get("id") or "").strip()
        iso2 = (item.get("iso2Code") or "").strip()
        name = (item.get("name") or "").strip()
        reg_obj = item.get("region") or {}
        inc_obj = item.get("incomeLevel") or {}
        lend_obj = item.get("lendingType") or {}

        reg_id = (reg_obj.get("id") or "").strip()
        inc_id = (inc_obj.get("id") or "").strip()
        lend_id = (lend_obj.get("id") or "").strip()
        # 世界银行将地区/收入组等聚合实体的 region.id 标记为 "NA"
        is_aggregate = reg_id == "NA"

        if not include_aggregates and is_aggregate:
            continue
        if region_filter and reg_id.upper() != region_filter:
            continue
        if income_filter and inc_id.upper() != income_filter:
            continue
        if lending_filter and lend_id.upper() != lending_filter:
            continue
        if q and not (
            q in name.lower()
            or q == code.lower()
            or q == iso2.lower()
        ):
            continue

        out.append(
            {
                "id": code,
                "iso2": iso2 or None,
                "name": name,
                "region_id": reg_id or None,
                "region": (reg_obj.get("value") or "").strip() or None,
                "income_level_id": inc_id or None,
                "income_level": (inc_obj.get("value") or "").strip() or None,
                "lending_type_id": lend_id or None,
                "lending_type": (lend_obj.get("value") or "").strip() or None,
                "capital_city": (item.get("capitalCity") or "").strip() or None,
                "longitude": _float_or_none(item.get("longitude")),
                "latitude": _float_or_none(item.get("latitude")),
                "is_aggregate": is_aggregate,
            }
        )

    # 精确匹配代码或名称排最前，其次国家实体优先于聚合地区
    out.sort(
        key=lambda x: (
            not (q and (x["id"].lower() == q or (x["iso2"] or "").lower() == q or x["name"].lower() == q)),
            x["is_aggregate"],
            x["id"],
        )
    )
    capped_limit = max(1, min(_int_or(limit, 50), 400))
    return {
        "query": query,
        "count": len(out),
        "countries": out[:capped_limit],
    }


def list_topics(language: str = "en") -> dict[str, Any]:
    """列出世界银行全部数据主题（Topics）。"""
    meta, items = _request_v2("topic", {"per_page": 100}, language=language)
    topics = [
        {
            "id": str(item.get("id", "")).strip(),
            "name": (item.get("value") or "").strip(),
            "source_note": (item.get("sourceNote") or "").strip(),
        }
        for item in items
    ]
    return {
        "pagination": _format_pagination(meta),
        "count": len(topics),
        "topics": topics,
    }


def list_sources(query: str = "", language: str = "en") -> dict[str, Any]:
    """列出世界银行全部数据库/数据源（Sources），支持关键词过滤。"""
    meta, items = _request_v2("sources", {"per_page": 200}, language=language)
    q = (query or "").strip().lower()
    sources: list[dict[str, Any]] = []
    for item in items:
        name = (item.get("name") or "").strip()
        code = (item.get("code") or "").strip()
        desc = (item.get("description") or "").strip()
        sid = str(item.get("id", "")).strip()
        if q and q not in f"{sid} {code} {name} {desc}".lower():
            continue
        sources.append(
            {
                "id": sid,
                "code": code,
                "name": name,
                "description": desc,
                "url": (item.get("url") or "").strip(),
                "last_updated": (item.get("lastupdated") or "").strip(),
                "data_availability": (item.get("dataavailability") or "").strip(),
                "metadata_availability": (item.get("metadataavailability") or "").strip(),
            }
        )
    return {
        "query": query,
        "pagination": _format_pagination(meta),
        "count": len(sources),
        "sources": sources,
    }


def list_classifications(
    classification_type: str = "incomeLevel",
    language: str = "en",
) -> dict[str, Any]:
    """列出世界银行分类参考代码表（region / incomeLevel / lendingType / languages）。"""
    allowed = {
        "region": "region",
        "incomeLevel": "incomeLevel",
        "lendingType": "lendingType",
        "languages": "languages",
    }
    endpoint = allowed.get(classification_type)
    if not endpoint:
        raise WorldBankError(
            f"不支持的分类类型：{classification_type!r}，可选值：{', '.join(allowed.keys())}"
        )

    meta, items = _request_v2(endpoint, {"per_page": 200}, language=language)
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if endpoint == "languages":
            cleaned.append(
                {
                    "code": (item.get("code") or "").strip(),
                    "name": (item.get("name") or "").strip(),
                    "native_form": (item.get("nativeForm") or "").strip(),
                }
            )
        elif endpoint == "region":
            cleaned.append(
                {
                    "id": str(item.get("id", "")).strip(),
                    "code": (item.get("code") or "").strip(),
                    "iso2": (item.get("iso2code") or "").strip(),
                    "name": (item.get("name") or "").strip(),
                }
            )
        else:
            cleaned.append(
                {
                    "id": (item.get("id") or "").strip(),
                    "iso2": (item.get("iso2code") or "").strip(),
                    "name": (item.get("value") or "").strip(),
                }
            )

    return {
        "classification_type": endpoint,
        "pagination": _format_pagination(meta),
        "count": len(cleaned),
        "items": cleaned,
    }


def health_check() -> dict[str, Any]:
    """通过最小数据请求检查世界银行 V2 API 连通性。"""
    try:
        meta, items = _request_v2(
            "country/CHN/indicator/SP.POP.TOTL",
            {"mrnev": 1, "per_page": 1},
        )
        sample = items[0] if items else {}
        return {
            "ok": True,
            "api_version": "v2",
            "auth_required": False,
            "last_updated": meta.get("lastupdated"),
            "sample": {
                "country": (sample.get("country") or {}).get("value"),
                "indicator": (sample.get("indicator") or {}).get("id"),
                "date": sample.get("date"),
                "value": sample.get("value"),
            },
            "message": "世界银行 Indicators API v2 连通正常（无需密钥）。",
        }
    except WorldBankError as exc:
        return {
            "ok": False,
            "api_version": "v2",
            "auth_required": False,
            "message": f"连通性检查失败：{exc}",
        }


# ------------------------------------------------------------------ 工具注册表

TOOLS: dict[str, dict[str, Any]] = {
    "wb_indicator_data": {
        "fn": indicator_data,
        "desc": (
            "查询世界银行（World Bank Indicators API v2）时间序列指标数据，无需 API Key。"
            "支持多国（分号分隔如 CHN;USA 或 all）、多指标（分号分隔，最多 60 个）、"
            "年份/月度/季度范围（date）以及最近 N 期非空值（mrnev）。"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "country": {
                    "type": "string",
                    "description": "国家/地区 ISO3 或 ISO2 代码，多国用分号分隔（如 CHN;USA;JPN），全部国家填 all。",
                },
                "indicator": {
                    "type": "string",
                    "description": "指标代码，如 NY.GDP.MKTP.CD（GDP）、SP.POP.TOTL（人口）；多指标用分号分隔（最多 60 个）。",
                },
                "date": {
                    "type": "string",
                    "description": "时间或范围：单年 2023、区间 2015:2024、月度 2023M01:2023M12、季度 2023Q1:2023Q4、YTD:2024。",
                },
                "mrv": {
                    "type": "integer",
                    "description": "返回最近 N 期数据（包含可能为空的期次）。",
                },
                "mrnev": {
                    "type": "integer",
                    "description": "返回最近 N 期非空数据（推荐用于获取最新可用观测值）。",
                },
                "gapfill": {
                    "type": "string",
                    "enum": ["Y", "N"],
                    "description": "与 mrv 联用：Y 表示空值向前一期回填。",
                },
                "frequency": {
                    "type": "string",
                    "enum": ["Y", "Q", "M"],
                    "description": "与 mrv 联用，指定高频数据频率：Y=年，Q=季，M=月。",
                },
                "source": {
                    "type": "string",
                    "description": "数据源 ID（如 2=世界发展指标 WDI）。多指标查询未指定时默认 2。",
                },
                "footnote": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否返回数据脚注（footnote=y）。",
                },
                "language": {
                    "type": "string",
                    "default": "en",
                    "description": "返回语言代码：en（英文）、zh（中文）、es、fr、ar 等。",
                },
                "page": {
                    "type": "integer",
                    "default": 1,
                    "description": "分页页码（从 1 开始）。",
                },
                "per_page": {
                    "type": "integer",
                    "default": 100,
                    "description": "每页返回条数（1..5000）。",
                },
                "exclude_nulls": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否过滤掉 value 为 null 的空观测记录。",
                },
            },
            "required": ["country", "indicator"],
        },
    },
    "wb_search_indicators": {
        "fn": search_indicators,
        "desc": (
            "按关键词、数据源（默认 WDI source=2）或主题 ID 检索世界银行指标代码与说明。"
            "在不确定准确指标代码时优先调用本工具。"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "default": "",
                    "description": "搜索关键词（支持多词空格分隔，如 'gdp per capita'、'inflation'、'co2'）。",
                },
                "source_id": {
                    "type": "string",
                    "description": "数据源 ID（默认 2=WDI；可由 wb_list_sources 获取）。",
                },
                "topic_id": {
                    "type": "string",
                    "description": "主题 ID（如 3=经济与增长；可由 wb_list_topics 获取，传入后优先按主题检索）。",
                },
                "language": {
                    "type": "string",
                    "default": "en",
                    "description": "返回语言代码（en/zh 等；英文指标库覆盖最全）。",
                },
                "limit": {
                    "type": "integer",
                    "default": 25,
                    "description": "返回条数上限（1..200）。",
                },
            },
            "required": [],
        },
    },
    "wb_indicator_info": {
        "fn": indicator_info,
        "desc": "查询指定指标代码的详细定义（source_note）、统计口径、数据来源机构与所属主题。",
        "schema": {
            "type": "object",
            "properties": {
                "indicator": {
                    "type": "string",
                    "description": "指标代码，如 NY.GDP.MKTP.CD。",
                },
                "source": {
                    "type": "string",
                    "description": "可选数据源 ID（指标隶属多个数据库时用于指定来源）。",
                },
                "language": {
                    "type": "string",
                    "default": "en",
                    "description": "返回语言代码（en/zh 等）。",
                },
            },
            "required": ["indicator"],
        },
    },
    "wb_lookup_countries": {
        "fn": lookup_countries,
        "desc": "按名称、ISO 代码、地理区域、收入组或贷款类型查询国家/地区代码及基本信息。",
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "default": "",
                    "description": "国家名称、ISO2 或 ISO3 代码，如 china、CN、CHN、中国。",
                },
                "region": {
                    "type": "string",
                    "description": "区域代码过滤，如 EAS（东亚与太平洋）、ECS（欧洲与中亚）。",
                },
                "income_level": {
                    "type": "string",
                    "description": "收入组过滤：HIC（高收入）、UMC（中高收入）、LMC（中低收入）、LIC（低收入）。",
                },
                "lending_type": {
                    "type": "string",
                    "description": "贷款类型过滤：IBD、IDA、IDB、LNX。",
                },
                "include_aggregates": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否包含全球/区域/收入组等聚合实体（设为 false 仅返回具体国家/经济体）。",
                },
                "language": {
                    "type": "string",
                    "default": "en",
                    "description": "返回语言代码（en/zh 等）。",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "description": "返回条数上限（1..400）。",
                },
            },
            "required": [],
        },
    },
    "wb_list_topics": {
        "fn": list_topics,
        "desc": "列出世界银行全部数据主题（Topics）及其 ID 与简介。",
        "schema": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "default": "en",
                    "description": "返回语言代码（en/zh 等）。",
                },
            },
            "required": [],
        },
    },
    "wb_list_sources": {
        "fn": list_sources,
        "desc": "列出世界银行全部数据库/数据源（Sources）及其 ID、代码和最后更新日期。",
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "default": "",
                    "description": "按数据库名称、代码或描述过滤关键词。",
                },
                "language": {
                    "type": "string",
                    "default": "en",
                    "description": "返回语言代码（en/zh 等）。",
                },
            },
            "required": [],
        },
    },
    "wb_list_classifications": {
        "fn": list_classifications,
        "desc": "列出世界银行分类参考代码表：region（区域）、incomeLevel（收入组）、lendingType（贷款类型）、languages（支持语言）。",
        "schema": {
            "type": "object",
            "properties": {
                "classification_type": {
                    "type": "string",
                    "enum": ["region", "incomeLevel", "lendingType", "languages"],
                    "default": "incomeLevel",
                    "description": "要查询的分类表类型。",
                },
                "language": {
                    "type": "string",
                    "default": "en",
                    "description": "返回语言代码（en/zh 等）。",
                },
            },
            "required": [],
        },
    },
    "wb_health_check": {
        "fn": health_check,
        "desc": "检查世界银行 Indicators API v2 网络连通性并返回示例最新数据更新时间。",
        "schema": {"type": "object", "properties": {}, "required": []},
    },
}


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """统一分发工具调用并捕获异常，保证返回结构一致。"""
    spec = TOOLS.get(name)
    if not spec:
        return {"ok": False, "tool": name, "error": f"unknown tool: {name}"}
    fn: Callable[..., dict[str, Any]] = spec["fn"]
    try:
        result = fn(**args)
        return {"ok": True, "tool": name, "result": result}
    except (WorldBankError, TypeError, ValueError) as exc:
        return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in tool %s", name)
        return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ MCP stdio

def _force_utf8_stdio() -> None:
    """Windows 管道默认可能为 GBK，强制切换为 UTF-8 以满足 MCP 协议要求。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _reply(obj: dict[str, Any]) -> None:
    """向 stdout 写入单行 JSON-RPC 响应并立即刷新缓冲区。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve() -> None:
    """运行基于 stdio 的 MCP JSON-RPC 服务器主循环。"""
    _force_utf8_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    },
                }
            )
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "tools/list":
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {"name": name, "description": spec["desc"], "inputSchema": spec["schema"]}
                            for name, spec in TOOLS.items()
                        ]
                    },
                }
            )
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            result = call_tool(name, args)
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                        ]
                    },
                }
            )
        elif method == "ping":
            _reply({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif msg_id is not None:
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )


# ------------------------------------------------------------------ CLI

def _parse_cli_bool(raw: str) -> bool:
    """解析 CLI 布尔参数。"""
    return raw.strip().lower() in ("1", "true", "yes", "y")


def cli(argv: list[str]) -> int:
    """提供独立命令行调用入口，便于调试与脚本集成。"""
    _force_utf8_stdio()
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write((__doc__ or "") + "\n")
        return 0

    cmd = argv[0]
    if cmd == "list":
        for name, spec in TOOLS.items():
            sys.stdout.write(f"{name:28} {spec['desc']}\n")
        return 0
    if cmd == "check":
        sys.stdout.write(
            json.dumps(call_tool("wb_health_check", {}), ensure_ascii=False, indent=2) + "\n"
        )
        return 0

    args: dict[str, Any] = {}
    if "--args-file" in argv:
        path = argv[argv.index("--args-file") + 1]
        args = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    elif "--json" in argv:
        idx = argv.index("--json")
        args = json.loads(argv[idx + 1])
    else:
        int_keys = {"mrv", "mrnev", "page", "per_page", "limit"}
        bool_keys = {"footnote", "exclude_nulls", "include_aggregates"}
        for i, token in enumerate(argv[1:], start=1):
            if token.startswith("--"):
                key = token[2:].replace("-", "_")
                val = argv[i + 1] if i + 1 < len(argv) else ""
                if key in int_keys:
                    args[key] = int(val)
                elif key in bool_keys:
                    args[key] = _parse_cli_bool(val)
                else:
                    args[key] = val

    sys.stdout.write(json.dumps(call_tool(cmd, args), ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(cli(sys.argv[1:]))
    serve()
