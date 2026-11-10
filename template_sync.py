"""Build the public routing template. No live subscription or VPS inputs."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import re
import ssl
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

UPSTREAM = "https://raw.githubusercontent.com/Aethersailor/Custom_OpenClash_Rules/main/cfg/yaml/Custom_Clash.yaml"
MAX_BYTES = 2 * 1024 * 1024
MANUAL = "🚀 手动选择"
DIRECT_GROUP = "🎯 全球直连"
CORE_GROUPS = (MANUAL, "🛬 LA落地", "🛰️ VPS直连", "✈️ 机场原始", DIRECT_GROUP)
ADBLOCK = "🛑 拦截广告"
DEFAULT_DIRECT = {"🍎 苹果服务", "Ⓜ️ 微软服务"}
BUILTINS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "PASS-RULE", "COMPATIBLE"}
HELPERS = {
    *CORE_GROUPS, *BUILTINS, ADBLOCK, "线路选择", "♻️ 自动选择", "🧠 Smart综合",
    "🛟 自动故障切换", "📶 弱网优先", "🌐 CDN综合", "☁️ CF优选", "🔁 反代优选",
    "⚖️ 反代负载均衡", "⚖️ 反代负载均衡-轮询", "⚖️ 反代负载均衡-粘滞",
    "⚖️ 反代负载均衡-散列", "🔄 反代轮询", "🧩 反代散列", "📌 反代粘滞",
}
PREFERRED = (
    "🤖 ChatGPT", "🤖 AI服务", "🎮 Steam", "🎮 游戏平台", "💬 即时通讯", "🌐 社交媒体",
    "🚀 GitHub", "📢 谷歌FCM", "🇬 谷歌服务", "🍎 苹果服务", "Ⓜ️ 微软服务", "📹 YouTube",
    "🎶 TikTok", "🎥 Netflix", "🎥 DisneyPlus", "🎥 HBO", "🎥 PrimeVideo", "🎥 AppleTV+",
    "🎥 Emby", "🎻 Spotify", "📺 Bahamut", "🌎 国外媒体", "🛒 国外电商", "🚀 测速工具",
    "🐟 漏网之鱼", "🔀 非标端口",
)
GEO_KEYS = ("geox-url", "geo-auto-update", "geo-update-interval", "geodata-mode")
PROVIDER_KEYS = {"type", "behavior", "format", "interval", "url", "path", "proxy", "payload", "size-limit"}


class InvalidTemplate(ValueError):
    """Messages contain structural reasons, never arbitrary YAML or URLs."""


class UniqueKeys(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        # Check explicit keys; the YAML merge operator may intentionally override defaults.
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=False)
            if not isinstance(key, (str, int, bool)) or key in seen:
                raise InvalidTemplate("YAML 包含重复或非法字段")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def read_yaml(raw: bytes, *, limit: int = MAX_BYTES) -> dict[str, Any]:
    if not 0 < len(raw) <= limit:
        raise InvalidTemplate("模板长度不合格")
    try:
        body = raw.decode("utf-8-sig")
        if sum(isinstance(event, yaml.AliasEvent) for event in yaml.parse(body)) > 512:
            raise InvalidTemplate("模板包含过多 YAML 别名")
        value = yaml.load(body, Loader=UniqueKeys)
    except (UnicodeError, yaml.YAMLError, RecursionError, TypeError):
        raise InvalidTemplate("模板不是有效 UTF-8 YAML") from None
    if not isinstance(value, dict):
        raise InvalidTemplate("模板顶层必须为映射")
    return value


def clean_name(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "," in value:
        raise InvalidTemplate("策略或资源名称无效")
    if any(not (char.isprintable() or char == "\u200d") for char in value):
        raise InvalidTemplate("策略或资源名称含控制字符")
    return value


def public_https(value: Any) -> str:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise InvalidTemplate("公开资源地址格式无效")
    try:
        uri = urllib.parse.urlsplit(value)
        hostname = (uri.hostname or "").lower().rstrip(".")
        port = uri.port
    except ValueError:
        raise InvalidTemplate("公开资源地址格式无效") from None
    if (uri.scheme != "https" or not hostname or "." not in hostname
            or hostname.endswith((".localhost", ".local", ".internal", ".lan"))
            or uri.username is not None or uri.password is not None
            or uri.query or uri.fragment or port not in (None, 443)):
        raise InvalidTemplate("公开资源必须是无认证参数的公网 HTTPS 地址")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return value
    raise InvalidTemplate("公开资源地址必须使用域名")


def fields(rule: str) -> list[str]:
    """Split a rule without splitting nested logical conditions."""
    if not isinstance(rule, str) or not rule or "\r" in rule or "\n" in rule:
        raise InvalidTemplate("分流规则格式无效")
    result, begin, depth, escaped = [], 0, 0, False
    for index, char in enumerate(rule):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise InvalidTemplate("分流规则括号不匹配")
        elif char == "," and depth == 0:
            result.append(rule[begin:index].strip())
            begin = index + 1
    result.append(rule[begin:].strip())
    if depth or len(result) < 2 or not result[0] or not result[-1]:
        raise InvalidTemplate("分流规则缺少合法目标")
    return result


def acyclic(graph: dict[str, list[str]], kind: str) -> None:
    active, done = set(), set()

    def visit(name: str) -> None:
        if name in active:
            raise InvalidTemplate(f"{kind}出现循环引用")
        if name in done:
            return
        active.add(name)
        for dest in graph.get(name, []):
            if dest in graph:
                visit(dest)
        active.remove(name)
        done.add(name)

    try:
        for name in graph:
            visit(name)
    except RecursionError:
        raise InvalidTemplate(f"{kind}引用层级过深") from None


def policy_targets(rule: str) -> tuple[str, str, list[str]]:
    parts = fields(rule)
    kind = parts[0]
    target = parts[-2] if parts[-1].lower() == "no-resolve" else parts[-1]
    if not target:
        raise InvalidTemplate("分流规则缺少目标")
    if kind == "RULE-SET":
        if len(parts) < 3:
            raise InvalidTemplate("RULE-SET 缺少规则集或目标")
        refs = [parts[1]]
    elif kind in {"SUB-RULE", "AND", "OR", "NOT"}:
        refs = re.findall(r"\(\s*RULE-SET\s*,\s*([^,()]+)", ",".join(parts[1:]))
        refs = [name.strip() for name in refs]
    else:
        refs = []
    return kind, target, refs


def validate_routes(config: dict[str, Any], *, public: bool = False) -> None:
    groups = config.get("proxy-groups") or []
    proxies = config.get("proxies") or []
    group_names = [clean_name(group["name"]) for group in groups]
    # Node display names are not parsed as comma-separated rule targets.
    proxy_names = [proxy["name"] for proxy in proxies]
    if any(not isinstance(name, str) or not name for name in proxy_names):
        raise InvalidTemplate("节点名称无效")
    if len(set(group_names + proxy_names)) != len(group_names + proxy_names):
        raise InvalidTemplate("节点或策略组重名")
    if set(group_names + proxy_names) & BUILTINS:
        raise InvalidTemplate("节点或策略组与内置策略重名")
    known = set(group_names + proxy_names) | BUILTINS | (set(CORE_GROUPS) if public else set())
    graph = {}
    for group in groups:
        members = group.get("proxies")
        if not isinstance(members, list) or not members or any(
            not isinstance(name, str) or name not in known for name in members
        ):
            raise InvalidTemplate("策略组引用不存在的成员")
        if "default-selected" in group and group["default-selected"] not in members:
            raise InvalidTemplate("策略组默认选项无效")
        graph[group["name"]] = members
    for proxy in proxies:
        dialer = proxy.get("dialer-proxy")
        if dialer is not None:
            if not isinstance(dialer, str) or dialer not in known:
                raise InvalidTemplate("落地节点引用不存在的第一跳")
            graph[proxy["name"]] = [dialer]
    acyclic(graph, "策略组或拨号节点")

    providers = config.get("rule-providers")
    if not isinstance(providers, dict) or not providers:
        raise InvalidTemplate("缺少规则集")
    for provider in providers.values():
        if provider.get("proxy", "DIRECT") not in known:
            raise InvalidTemplate("规则集下载引用不存在的策略组")
    subs = config.get("sub-rules", {})
    if not isinstance(subs, dict) or any(not isinstance(value, list) or not value for value in subs.values()):
        raise InvalidTemplate("子规则格式无效")
    sub_graph = {name: [] for name in subs}
    rules = config.get("rules")
    if not isinstance(rules, list) or not rules:
        raise InvalidTemplate("模板缺少分流规则")
    if fields(rules[-1])[0] != "MATCH" or len(fields(rules[-1])) != 2:
        raise InvalidTemplate("最后一条分流规则必须是 MATCH")
    for scope, entries in [(None, rules), *subs.items()]:
        for rule in entries:
            kind, target, refs = policy_targets(rule)
            if any(ref not in providers for ref in refs):
                raise InvalidTemplate("分流规则引用不存在的规则集")
            if kind == "SUB-RULE":
                if target not in subs:
                    raise InvalidTemplate("分流规则引用不存在的子规则")
                if scope is not None:
                    sub_graph[scope].append(target)
            elif target not in known:
                raise InvalidTemplate("分流规则仍引用已删除或不存在的策略组")
    acyclic(sub_graph, "子规则")


def provider_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or set(value) - PROVIDER_KEYS:
        raise InvalidTemplate("规则集包含不可公开或未知的字段")
    p = dict(value)
    if p.get("type") not in {"http", "inline"}:
        raise InvalidTemplate("公开模板仅支持 HTTPS 或内联规则集")
    if p["type"] == "http":
        p["url"] = public_https(p.get("url"))
        if "payload" in p:
            raise InvalidTemplate("HTTPS 规则集不应同时包含本地内容")
    elif "url" in p or not isinstance(p.get("payload"), list) or not all(
        isinstance(item, str) for item in p["payload"]
    ):
        raise InvalidTemplate("内联规则集内容无效")
    if "path" in p:
        path = p["path"]
        if not isinstance(path, str) or ":" in path or path.startswith(("/", "\\", "~")) or ".." in re.split(r"[/\\]", path):
            raise InvalidTemplate("公开模板不允许绝对或越界规则文件路径")
    for name, item in p.items():
        if name != "payload" and not isinstance(item, (str, int)):
            raise InvalidTemplate("规则集字段类型无效")
    return p


def normalize(upstream: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    groups = upstream.get("proxy-groups")
    if not isinstance(groups, list) or not groups:
        raise InvalidTemplate("上游没有策略组")
    seen, names = set(), []
    for group in groups:
        if not isinstance(group, dict):
            raise InvalidTemplate("上游策略组格式无效")
        name = clean_name(group.get("name"))
        if name in seen:
            raise InvalidTemplate("上游策略组名称重复")
        seen.add(name)
        if name in HELPERS or (name.endswith("节点") and group.get("type") == "url-test"):
            continue
        names.append(name)
    old_order = PREFERRED if previous is None else tuple(
        group["name"] for group in previous["proxy-groups"] if group["name"] != DIRECT_GROUP
    )
    name_set = set(names)
    if previous is not None:
        removed = set(old_order) - name_set
        for group in groups:
            members = group.get("proxies", [])
            if group["name"] in name_set and isinstance(members, list) and any(
                isinstance(member, str) and member in removed for member in members
            ):
                raise InvalidTemplate("上游策略组仍引用已删除的业务组")
    ordered = [name for name in old_order if name in name_set]
    survivors = set(ordered)
    ordered.extend(name for name in names if name not in survivors)
    result: dict[str, Any] = {}
    for key in GEO_KEYS:
        if key not in upstream:
            continue
        value = upstream[key]
        if key == "geox-url":
            if not isinstance(value, dict):
                raise InvalidTemplate("地理数据地址格式无效")
            result[key] = {clean_name(name): public_https(url) for name, url in value.items()}
        elif key == "geo-update-interval":
            if type(value) is not int or value < 1:
                raise InvalidTemplate("地理数据更新时间无效")
            result[key] = value
        elif type(value) is bool:
            result[key] = value
        else:
            raise InvalidTemplate("地理数据选项无效")
    result["proxy-groups"] = [
        {"name": name, "type": "select", "proxies": [MANUAL, DIRECT_GROUP],
         **({"default-selected": DIRECT_GROUP} if name in DEFAULT_DIRECT else {})}
        for name in ordered
    ] + [{"name": DIRECT_GROUP, "type": "select", "proxies": ["DIRECT"]}]
    if not isinstance(upstream.get("rules"), list):
        raise InvalidTemplate("缺少上游分流规则")
    result["rules"] = list(upstream["rules"])
    if not isinstance(upstream.get("rule-providers"), dict):
        raise InvalidTemplate("缺少上游规则集")
    result["rule-providers"] = {
        clean_name(name): provider_projection(provider)
        for name, provider in upstream["rule-providers"].items()
    }
    if "sub-rules" in upstream:
        if not isinstance(upstream["sub-rules"], dict):
            raise InvalidTemplate("上游子规则格式无效")
        result["sub-rules"] = {clean_name(k): v for k, v in upstream["sub-rules"].items()}
    validate_routes(result, public=True)
    return result


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str) -> bytes:
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), PublicRedirect()
    )
    with opener.open(public_https(url), timeout=20) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise InvalidTemplate("上游模板超过大小上限")
    return data


def encoded(template: dict[str, Any]) -> bytes:
    header = (
        "# Public routing skeleton, not a complete subscription.\n"
        "# Adapted from Aethersailor/Custom_OpenClash_Rules (CC BY-SA 4.0).\n"
        "# https://github.com/Aethersailor/Custom_OpenClash_Rules/blob/main/cfg/yaml/Custom_Clash.yaml\n"
        "# Changes: retain public routing fields, normalize business groups and order.\n"
    )
    return (header + yaml.safe_dump(template, sort_keys=False, allow_unicode=True, width=4096)).encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            staged.unlink(missing_ok=True)
            raise
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=UPSTREAM)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("template.yaml"))
    args = parser.parse_args()
    try:
        old = args.output.read_bytes() if args.output.is_file() else None
        previous = read_yaml(old) if old else None
        if previous is not None:
            validate_routes(previous, public=True)
        raw = fetch(args.source) if args.source.startswith("https://") else Path(args.source).read_bytes()
        candidate = normalize(read_yaml(raw), previous)
        data = encoded(candidate)
        if old != data:
            atomic_write(args.output, data)
        print(f"changed={old != data} business_groups={len(candidate['proxy-groups'])-1} "
              f"rules={len(candidate['rules'])} sha256={hashlib.sha256(data).hexdigest()}")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(str(exc) if isinstance(exc, InvalidTemplate) else "模板检查或更新失败，保留上一版", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
