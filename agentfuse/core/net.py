"""Network helpers of the local runtime.

The dashboard has **no** authentication - it is a local tool.  Binding it to
a public address would expose the runtime to everybody in the network, so
every entry point has to check the address before it serves.
"""

from __future__ import annotations


def is_loopback(host: str) -> bool:
    """True when the address only reaches the local machine."""

    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "", "::1")


def require_loopback(host: str, allow_remote: bool = False) -> None:
    """Raise a ``PermissionError`` when the address is not a loopback address.

    ``allow_remote`` is the explicit confirmation of the user - the dashboard
    has no authentication, therefore it must never be exposed by accident.
    """

    if is_loopback(host) or allow_remote:
        return
    raise PermissionError(
        f"{host} 不是回环地址。管理界面没有认证，"
        "对外网暴露会让任何人都能操作 runtime。"
        "\n确认请加上 --allow-remote。"
    )
