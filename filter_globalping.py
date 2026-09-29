"""
中国视角实测过滤脚本：通过 Globalping 的中国大陆探针测量候选 IP 的真实落地机房。

读取 collect_ips.py 抓取的 candidates.txt，用位于中国大陆的探针逐个IP发起 HTTPS 请求，
读取 Cloudflare /cdn-cgi/trace 返回的真实 colo，排除香港(HKG)等指定机房后，
生成 ip.txt（TLS端口）和 notslip.txt（非TLS端口）。

为什么用 Globalping，而不是直接在本脚本里连：
    Cloudflare 是任播(anycast)，同一个 IP 从不同网络接入会落到不同机房。
    GitHub 官方 runner 全部在境外（实测 colo 多为 IAD），测出来的落点对中国用户毫无意义；
    数据源标注的机房则是它自己测试机的结果，也不代表你。
    Globalping 在国内有 50+ 个在线探针（移动/联通/电信/阿里/腾讯），
    通过它的 API 就能让"从中国实测"这一步也留在 GitHub Actions 里完成，无需本地操作。

实测一致性：
    所有候选 IP 都交给同一个探针去测（先用一次种子测量锁定探针，再把它的 id
    作为后续测量的 locations）。否则每个 IP 随机落到不同城市/运营商的探针上，
    落点不可比，筛选结果没有意义。

用法：
    python filter_globalping.py

可选环境变量：
    GLOBALPING_TOKEN   Globalping 个人访问令牌（https://dash.globalping.io/tokens）
                       不填也能跑。未认证时创建测量的限流按出口 IP 计算，
                       GitHub 共享出口 IP 偶发会被限流，填个免费令牌更稳。
"""

import json
import os
import re
import random
import time
import urllib.error
import urllib.request

API = 'https://api.globalping.io/v1'
TOKEN = os.environ.get('GLOBALPING_TOKEN', '').strip()

# 候选文件，由 collect_ips.py 生成
CANDIDATES_FILE = 'candidates.txt'

# 需要排除的机房代码（IATA），香港=HKG，可按需追加
EXCLUDE_DATACENTERS = {'HKG'}

# 探针选择：按顺序尝试，第一个有在线探针的运营商胜出，然后固定用它测所有IP。
# 9808 是中国移动骨干，其探针多为 eyeball-network（家宽），最接近家用宽带的落点。
PROBE_ASNS = [
    9808, 56046, 56041, 24400, 24445,   # 中国移动
    4837, 17621, 9929,                   # 中国联通
    4134,                                # 中国电信
]
PROBE_FALLBACK = {'country': 'CN'}       # 上面都没有就用全部国内探针

# 实测用的 Cloudflare 站点（须为 CF 上的域名，用它固定 SNI 去连候选IP）
MEASURE_HOST = 'www.cloudflare.com'
MEASURE_PATH = '/cdn-cgi/trace'

POLL_INTERVAL = 0.6      # 官方要求两次轮询间隔 >= 0.5s，否则触发 2 req/s 限流
POLL_MAX_TIMES = 40      # 最多轮询次数（约 24 秒）
HTTP_TIMEOUT = 20
SEED_MAX_AGE = 150       # 种子测量 id 的保质期（秒），超了就重新锁定探针

# TLS端口（写入ip.txt）
tsl_ports = ["443", "8443", "2053", "2083", "2087", "2096"]
# 非TLS端口（写入notslip.txt）
notsl_ports = ["80", "8080", "8880", "2052", "2082", "2086", "2095"]

ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'


def api_request(method, path, payload=None, retry_429=4):
    """调用 Globalping API，返回解析后的 JSON。遇到 429 按 Retry-After 退避重试。"""
    url = API + path
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    headers = {'Accept': 'application/json', 'User-Agent': 'CloudflareIp/1.0'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if TOKEN:
        headers['Authorization'] = 'Bearer ' + TOKEN

    for attempt in range(retry_429 + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode('utf-8', 'ignore'))
        except urllib.error.HTTPError as err:
            body = err.read().decode('utf-8', 'ignore')
            if err.code == 429 and attempt < retry_429:
                wait = int(err.headers.get('Retry-After') or 5) + 1
                print(f'  触发限流，等待 {wait} 秒后重试...')
                time.sleep(wait)
                continue
            raise RuntimeError(f'HTTP {err.code} {body[:200]}')
        except Exception as err:
            raise RuntimeError(str(err))
    raise RuntimeError('重试次数已用尽')


def create_measurement(payload):
    return api_request('POST', '/measurements', payload)


def wait_measurement(measurement_id):
    """轮询直到测量结束，返回最终结果。"""
    data = None
    for _ in range(POLL_MAX_TIMES):
        data = api_request('GET', '/measurements/' + measurement_id)
        if data.get('status') in ('finished', 'failed'):
            return data
        time.sleep(POLL_INTERVAL)
    return data


def lock_probe():
    """
    建一次种子测量锁定单个探针。
    返回 (种子测量id, 探针信息)。后续所有候选 IP 都用这个 id，确保 vantage 一致。
    """
    attempts = [{'country': 'CN', 'asn': asn} for asn in PROBE_ASNS] + [PROBE_FALLBACK]
    last_error = None
    for location in attempts:
        who = f"AS{location['asn']}" if 'asn' in location else '国内任意运营商'
        try:
            seed = create_measurement({
                'type': 'ping',
                'target': MEASURE_HOST,
                'locations': [location],
                'limit': 1,
            })
            data = wait_measurement(seed['id'])
        except RuntimeError as err:
            print(f'  {who} 建测量失败: {err}')
            last_error = err
            continue

        results = data.get('results') or []
        if not results:
            print(f'  {who} 暂时没有在线探针，换下一个运营商')
            continue

        probe = results[0].get('probe') or {}
        print(f'已锁定探针: {probe.get("city", "?")} / {probe.get("network", "?")} (AS{probe.get("asn", "?")})')
        return seed['id'], probe

    raise RuntimeError(f'Globalping 目前没有可用的中国大陆探针: {last_error}')


def measure_colo(probe_id, ip):
    """让锁定的探针去连候选IP，读取 /cdn-cgi/trace 里的真实 colo，失败返回 None。"""
    payload = {
        'type': 'http',
        'target': ip,
        'locations': probe_id,
        'measurementOptions': {
            'protocol': 'HTTPS',
            'port': 443,
            'request': {'host': MEASURE_HOST, 'path': MEASURE_PATH, 'method': 'GET'},
        },
    }
    try:
        created = create_measurement(payload)
    except RuntimeError as err:
        # 种子测量 id 失效时 API 返回 422，交给外层重新锁定探针后重试
        if 'HTTP 422' in str(err):
            raise
        print(f'  {ip} 创建测量失败: {err}')
        return None

    data = wait_measurement(created['id'])
    result = ((data.get('results') or [{}])[0]).get('result') or {}
    if result.get('status') != 'finished':
        return None

    body = result.get('rawBody') or result.get('rawOutput') or ''
    found = re.search(r'^colo=(\S+)', body, re.M)
    return found.group(1) if found else None


# ---------------------------------------------------------------- 读取候选

if not os.path.exists(CANDIDATES_FILE):
    print(f'未找到 {CANDIDATES_FILE}，请先运行 collect_ips.py 抓取候选。')
    raise SystemExit(1)

candidates = []
with open(CANDIDATES_FILE, encoding='utf-8') as file:
    for line in file:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        found = re.search(ip_pattern, line)
        if found:
            candidates.append(found.group(0))

# 去重并保持原有顺序
ips = list(dict.fromkeys(candidates))
if not ips:
    print(f'{CANDIDATES_FILE} 里没有可用的IP。')
    raise SystemExit(1)

# ------------------------------------------------------- 从中国探针逐个实测

print(f'准备从中国大陆探针实测 {len(ips)} 个IP的落地机房...')
try:
    probe_id, probe_info = lock_probe()
except RuntimeError as err:
    print(f'锁定探针失败: {err}')
    print('未生成 ip.txt / notslip.txt（已有文件保持不变）。')
    raise SystemExit(1)

probe_name = f"{probe_info.get('city', '?')}/{probe_info.get('network', '?')}"
print(f'开始实测...')

colo_map = {}
failed_ips = []
seed_time = time.time()


def measure_with_reseed(ip):
    """实测单个IP；探针 id 过期（422）时自动重新锁定并重试一次。"""
    global probe_id, probe_info, probe_name, seed_time
    try:
        return measure_colo(probe_id, ip)
    except RuntimeError as err:
        if 'HTTP 422' not in str(err):
            print(f'  {ip} 创建测量失败: {err}')
            return None
        print(f'  {ip} 探针已过期，重新锁定后重试...')
        try:
            probe_id, probe_info = lock_probe()
        except RuntimeError as lock_err:
            print(f'  重新锁定探针失败: {lock_err}')
            raise SystemExit('探针不可用，未生成文件（已有文件保持不变）。')
        probe_name = f"{probe_info.get('city', '?')}/{probe_info.get('network', '?')}"
        seed_time = time.time()
        return measure_colo(probe_id, ip)


for index, ip in enumerate(ips, 1):
    colo = measure_with_reseed(ip)
    if not colo:
        failed_ips.append(ip)
        print(f'  [{index}/{len(ips)}] {ip:<16} 实测失败')
    else:
        colo_map[ip] = colo
        mark = '  <-- 已排除' if colo.upper() in EXCLUDE_DATACENTERS else ''
        print(f'  [{index}/{len(ips)}] {ip:<16} colo={colo}{mark}')

    # 种子测量 id 会过期，快到保质期就提前换一个新的，避免后面的 IP 批量失败
    if time.time() - seed_time > SEED_MAX_AGE and index < len(ips):
        print('  探针即将过期，提前重新锁定...')
        try:
            probe_id, probe_info = lock_probe()
            probe_name = f"{probe_info.get('city', '?')}/{probe_info.get('network', '?')}"
            seed_time = time.time()
        except RuntimeError as err:
            print(f'  重新锁定探针失败: {err}')
            print('未生成文件（已有文件保持不变）。')
            raise SystemExit(1)

kept = [ip for ip in ips if colo_map.get(ip) and colo_map[ip].upper() not in EXCLUDE_DATACENTERS]
excluded = [ip for ip in ips if colo_map.get(ip) and colo_map[ip].upper() in EXCLUDE_DATACENTERS]

print(f'实测成功 {len(kept) + len(excluded)}/{len(ips)} 个（探针：{probe_name}）')
if excluded:
    print(f'中国视角实测落在 {"/".join(sorted(EXCLUDE_DATACENTERS))} 已排除 {len(excluded)} 个: {", ".join(excluded)}')
if failed_ips:
    # 连不上或读不到机房的，一并剔除，避免漏掉香港
    print(f'实测失败已排除 {len(failed_ips)} 个: {", ".join(failed_ips)}')

if not kept:
    # 全部失败通常是探针或 API 出了问题，此时不覆盖已有的 ip.txt / notslip.txt
    print('过滤后没有可用IP，未生成文件（已有文件保持不变）。')
    raise SystemExit(1)

# ---------------------------------------------------------------- 写文件

with open('ip.txt', 'w', encoding='utf-8') as file:
    for ip in kept:
        file.write(f"{ip}:{random.choice(tsl_ports)}#{colo_map[ip]}\n")

with open('notslip.txt', 'w', encoding='utf-8') as file:
    for ip in kept:
        file.write(f"{ip}:{random.choice(notsl_ports)}#{colo_map[ip]}\n")

print(f'已生成 ip.txt / notslip.txt，共 {len(kept)} 个非香港IP（#后面是中国探针实测的落地机房）')
