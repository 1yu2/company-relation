"""外部企业数据补数的运行配置读取。"""


async def load_external_data_config() -> dict:
    """读取外部数据补数配置；失败时返回空字典使用默认值。"""
    try:
        from yuxi.config.options import external_company_data_opts

        return await external_company_data_opts.get()
    except Exception:
        return {}


def enabled_external_providers(config: dict) -> list[str]:
    """根据配置返回启用的 Provider 列表。"""
    enabled = []
    if config.get("akshare_enabled", False):
        enabled.append("akshare")
    if config.get("public_disclosure_enabled", False):
        enabled.append("public_disclosure")
    if config.get("financing_enabled", False):
        enabled.append("financing")
    return enabled


def config_value(config: dict, key: str, default):
    """读取允许为 0 或 False 的配置值。"""
    value = config.get(key)
    return default if value is None else value
