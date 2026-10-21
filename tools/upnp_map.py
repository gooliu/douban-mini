# -*- coding: utf-8 -*-
"""
UPnP 自动端口映射工具（无需路由器密码）
用法:
    python upnp_map.py            # 自动发现网关并映射 8000 -> 本机:8000
    python upnp_map.py remove     # 删除映射
    python upnp_map.py status     # 查询 WAN 公网 IP 与现有映射
仅用 Python 标准库实现（SSDP + SOAP）。
"""
import socket
import sys
import re
import urllib.request
import urllib.error

SSDP_ADDR = ("239.255.255.250", 1900)
MSEARCH = (
    'M-SEARCH * HTTP/1.1\r\n'
    'HOST: 239.255.255.250:1900\r\n'
    'MAN: "ssdp:discover"\r\n'
    'MX: 2\r\n'
    'ST: {st}\r\n\r\n'
)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def ssdp_discover(st="urn:schemas-upnp-org:device:InternetGatewayDevice:1", timeout=3.0):
    """返回 [(location_url, st)]"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        sock.sendto(MSEARCH.format(st=st).encode(), SSDP_ADDR)
    except Exception:
        return []
    found = []
    deadline = socket.getdefaulttimeout()
    import time
    end = time.time() + timeout
    while time.time() < end:
        try:
            sock.settimeout(max(0.1, end - time.time()))
            data, _ = sock.recvfrom(65507)
        except Exception:
            break
        text = data.decode("utf-8", "ignore")
        m = re.search(r"^LOCATION:\s*(\S+)", text, re.M | re.I)
        if m:
            found.append(m.group(1).strip())
    sock.close()
    return list(dict.fromkeys(found))


def http_get(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 upnp-map"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def parse_services(desc):
    """解析设备描述 XML，返回 [(serviceType, controlURL, SCPDURL)]"""
    base = None
    m = re.search(r"<URLBase>(.*?)</URLBase>", desc, re.S)
    if m:
        base = m.group(1).strip()
    out = []
    for svc in re.findall(r"<service>(.*?)</service>", desc, re.S):
        t = re.search(r"<serviceType>(.*?)</serviceType>", svc, re.S)
        c = re.search(r"<controlURL>(.*?)</controlURL>", svc, re.S)
        if t and c:
            out.append((t.group(1).strip(), c.group(1).strip(), base))
    return out


def abs_url(control, base, location):
    if control.startswith("http"):
        return control
    root = base or location.rsplit("/", 1)[0] if "/" in location else location
    # 拼接
    if not root.endswith("/"):
        root = root.rsplit("/", 1)[0] if control.startswith("/") else root
    if control.startswith("/"):
        # http://host:port/path 取 scheme+host
        m = re.match(r"(https?://[^/]+)", root)
        host = m.group(1) if m else root
        return host + control
    return root.rstrip("/") + "/" + control


def soap_post(url, service, action, args, timeout=8):
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service}">'
        + "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
        + f"</u:{action}></s:Body></s:Envelope>"
    )
    data = body.encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service}#{action}"',
            "User-Agent": "Mozilla/5.0 upnp-map",
            "Content-Length": str(len(data)),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def find_wan_conn():
    """返回 (control_url, service_type)"""
    locations = []
    for st in (
        "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
        "urn:schemas-upnp-org:device:WANConnectionDevice:1",
        "ssdp:all",
    ):
        locations = ssdp_discover(st, timeout=3.0)
        if locations:
            break
    if not locations:
        return None
    for loc in locations:
        try:
            desc = http_get(loc)
        except Exception:
            continue
        for stype, ctrl, base in parse_services(desc):
            if "WANIPConnection" in stype or "WANPPPConnection" in stype:
                return abs_url(ctrl, base, loc), stype
    return None


def add_mapping(ext_port, int_ip, int_port, desc="douban-mini"):
    conn = find_wan_conn()
    if not conn:
        print("[失败] 没有发现支持 UPnP 的网关（路由器可能关闭了 UPnP）")
        return 1
    url, stype = conn
    args = {
        "NewRemoteHost": "",
        "NewExternalPort": str(ext_port),
        "NewProtocol": "TCP",
        "NewInternalPort": str(int_port),
        "NewInternalClient": int_ip,
        "NewEnabled": "1",
        "NewPortMappingDescription": desc,
        "NewLeaseDuration": "0",
    }
    code, body = soap_post(url, stype, "AddPortMapping", args)
    if code == 200:
        print(f"[成功] 已映射 公网:{ext_port} -> {int_ip}:{int_port} (TCP)")
        return 0
    err = re.search(r"<errorDescription>(.*?)</errorDescription>", body, re.S)
    print(f"[失败] HTTP {code} {err.group(1) if err else body[:300]}")
    return 2


def remove_mapping(ext_port):
    conn = find_wan_conn()
    if not conn:
        print("[失败] 没有发现支持 UPnP 的网关")
        return 1
    url, stype = conn
    args = {"NewRemoteHost": "", "NewExternalPort": str(ext_port), "NewProtocol": "TCP"}
    code, body = soap_post(url, stype, "DeletePortMapping", args)
    print(f"[{'成功' if code == 200 else '失败'}] 删除映射 HTTP {code}")
    return 0 if code == 200 else 2


def status():
    conn = find_wan_conn()
    if not conn:
        print("[结果] 未发现 UPnP 网关 -> 路由器 UPnP 可能未开启")
        return 1
    url, stype = conn
    code, body = soap_post(url, stype, "GetExternalIPAddress", {})
    ip = re.search(r"<NewExternalIPAddress>(.*?)</NewExternalIPAddress>", body, re.S)
    wan = ip.group(1) if ip else "未知"
    print(f"WAN 口 IP: {wan}")
    if wan.startswith(("10.", "100.64", "100.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
                       "172.2", "172.30.", "172.31.")):
        print("判定: 运营商内网(CGNAT)，端口映射无效，需要穿透或云服务器")
    else:
        print("判定: 具备公网 IP，端口映射可用")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "add"
    ip = local_ip()
    print(f"本机内网 IP: {ip}")
    if cmd == "remove":
        sys.exit(remove_mapping(8000))
    if cmd == "status":
        sys.exit(status())
    sys.exit(add_mapping(8000, ip, 8000))
