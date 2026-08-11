"""企业尽调只读 MySQL 配置。"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Mapping
from urllib.parse import quote

from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url

MYSQL_URL_ENV = "MYSQL_URL"
REQUIRED_TABLES = (
    "t_enterprise",
    "listed_company_gd",
    "t_partners_alls",
    "t_company_products",
    "t_company_finance",
    "t_administrative_punishment",
    "t_huanbaochufas",
    "t_abnormal_enterprises",
    "t_license_info",
    "t_patents_relations_info",
    "t_trademark_relation",
    "t_copyrights_relations",
    "company_indicator",
    "wind_listed_company_metrics",
    "company_news",
    "company_news_interpret",
)


class MySQLConfigurationError(ValueError):
    """表示企业尽调 MySQL 配置无法安全解析。"""


class CompanyDueDiligenceMySQLConfig(BaseModel):
    """描述企业尽调只读 MySQL 的连接池和超时配置。"""

    url: str | None = None
    pool_size: int = Field(default=2, ge=1, le=20)
    max_overflow: int = Field(default=0, ge=0, le=20)
    pool_recycle_seconds: int = Field(default=300, ge=30, le=3600)
    pool_timeout_seconds: int = Field(default=5, ge=1, le=60)
    connect_timeout_seconds: int = Field(default=8, ge=1, le=60)
    statement_timeout_seconds: int = Field(default=10, ge=1, le=120)

    @property
    def configured(self) -> bool:
        """返回是否已经提供外部 MySQL 连接地址。"""
        return bool(self.url)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> CompanyDueDiligenceMySQLConfig:
        """从环境变量读取企业尽调 MySQL 配置。"""
        source = os.environ if environ is None else environ
        raw_url = source.get(MYSQL_URL_ENV, "")
        values = {
            "url": parse_mysql_url(raw_url),
            "pool_size": source.get("COMPANY_DD_MYSQL_POOL_SIZE", 2),
            "max_overflow": source.get("COMPANY_DD_MYSQL_MAX_OVERFLOW", 0),
            "pool_recycle_seconds": source.get("COMPANY_DD_MYSQL_POOL_RECYCLE_SECONDS", 300),
            "pool_timeout_seconds": source.get("COMPANY_DD_MYSQL_POOL_TIMEOUT_SECONDS", 5),
            "connect_timeout_seconds": source.get("COMPANY_DD_MYSQL_CONNECT_TIMEOUT_SECONDS", 8),
            "statement_timeout_seconds": source.get("COMPANY_DD_MYSQL_STATEMENT_TIMEOUT_SECONDS", 10),
        }
        return cls.model_validate(values)


def parse_mysql_url(raw_value: str | None) -> str | None:
    """安全解析标准 DSN 或历史 f-string 格式的 MySQL 地址。"""
    value = (raw_value or "").strip()
    if not value:
        return None

    if value.startswith(('f"', "f'")):
        value = _parse_legacy_fstring_url(value)
    elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]

    try:
        parsed = make_url(value)
    except Exception as exc:
        raise MySQLConfigurationError("MYSQL_URL 不是有效的 SQLAlchemy MySQL 地址") from exc

    if parsed.drivername != "mysql+aiomysql":
        raise MySQLConfigurationError("MYSQL_URL 必须使用 mysql+aiomysql 驱动")
    if not parsed.username or parsed.password is None or not parsed.host or not parsed.database:
        raise MySQLConfigurationError("MYSQL_URL 必须包含用户名、密码、主机和数据库名")

    return value


def redact_sensitive_text(message: str) -> str:
    """移除异常或日志文本中的 MySQL 地址和历史密码表达式。"""
    redacted = re.sub(
        r"f(?P<quote>['\"])mysql(?:\+(?:aiomysql|pymysql))?://.*?(?P=quote)",
        "<redacted-mysql-dsn>",
        str(message),
        flags=re.IGNORECASE | re.DOTALL,
    )
    redacted = re.sub(
        r"mysql(?:\+(?:aiomysql|pymysql))?://[^\s\"']+",
        "<redacted-mysql-dsn>",
        redacted,
        flags=re.IGNORECASE,
    )
    return re.sub(r"quote\((['\"]).*?\1\)", "quote('***')", redacted, flags=re.DOTALL)


def _parse_legacy_fstring_url(value: str) -> str:
    """解析唯一允许的 f-string DSN 结构而不执行其中表达式。"""
    try:
        expression = ast.parse(value, mode="eval").body
    except SyntaxError as exc:
        raise MySQLConfigurationError("MYSQL_URL 的 f-string 格式无效") from exc

    if not isinstance(expression, ast.JoinedStr) or len(expression.values) != 3:
        raise MySQLConfigurationError("MYSQL_URL 仅允许一个 quote('password') 插值")

    prefix_node, password_node, suffix_node = expression.values
    if not isinstance(prefix_node, ast.Constant) or not isinstance(prefix_node.value, str):
        raise MySQLConfigurationError("MYSQL_URL 缺少固定 DSN 前缀")
    if not isinstance(suffix_node, ast.Constant) or not isinstance(suffix_node.value, str):
        raise MySQLConfigurationError("MYSQL_URL 缺少固定 DSN 后缀")
    if not isinstance(password_node, ast.FormattedValue):
        raise MySQLConfigurationError("MYSQL_URL 密码必须使用 quote('password')")

    call = password_node.value
    valid_call = (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "quote"
        and len(call.args) == 1
        and not call.keywords
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    )
    if not valid_call:
        raise MySQLConfigurationError("MYSQL_URL 密码必须使用单个字符串参数 quote('password')")

    return f"{prefix_node.value}{quote(call.args[0].value, safe='')}{suffix_node.value}"
