"""企业尽调 MySQL 配置单元测试。"""

import pytest

from yuxi.company_due_diligence.config import (
    CompanyDueDiligenceMySQLConfig,
    MySQLConfigurationError,
    parse_mysql_url,
    redact_sensitive_text,
)


def test_parse_mysql_url_supports_legacy_quote_format():
    """历史 f-string 密码表达式应安全转换为标准 DSN。"""
    parsed = parse_mysql_url("f\"mysql+aiomysql://readonly:{quote('p@ss/word')}@mysql.example.com:3306/company\"")

    assert parsed == "mysql+aiomysql://readonly:p%40ss%2Fword@mysql.example.com:3306/company"


def test_parse_mysql_url_rejects_arbitrary_fstring_expression():
    """配置解析不得执行任意 Python 表达式。"""
    with pytest.raises(MySQLConfigurationError, match="quote"):
        parse_mysql_url("f\"mysql+aiomysql://readonly:{__import__('os').getcwd()}@mysql.example.com:3306/company\"")


def test_config_from_env_uses_readonly_pool_defaults():
    """缺省连接池参数应采用任务约定的小连接池。"""
    config = CompanyDueDiligenceMySQLConfig.from_env(
        {"MYSQL_URL": "mysql+aiomysql://readonly:password@mysql.example.com:3306/company"}
    )

    assert config.configured is True
    assert config.pool_size == 2
    assert config.max_overflow == 0
    assert config.pool_recycle_seconds == 300
    assert config.pool_timeout_seconds == 5


def test_redact_sensitive_text_removes_dsn_and_quote_password():
    """日志脱敏不得保留 DSN、账号、主机或密码。"""
    message = (
        "failed mysql+aiomysql://readonly:password@mysql.example.com:3306/company "
        "f\"mysql+aiomysql://readonly:{quote('password')}@mysql.example.com/company\""
    )

    redacted = redact_sensitive_text(message)

    assert "password" not in redacted
    assert "readonly" not in redacted
    assert "mysql.example.com" not in redacted
    assert "<redacted-mysql-dsn>" in redacted
