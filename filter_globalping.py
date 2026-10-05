"""
中国视角实测过滤脚本：通过 Globalping 的中国移动、中国联通探针测量候选 IP 的真实落地机房。

读取 collect_ips.py 抓取的 candidates.txt，把每个候选 IP 交给**一个中国移动探针和一个中国联通探针**
同时发起 HTTPS 请求，读取 Cloudflare /cdn-cgi/trace 返回的真实 colo。
只要有一边落在香港(HKG)，或者有一边没测出来，这个 IP 就丢弃；
两边都确认落在香港以外，才写进 ip.txt（TLS端口）和 notslip.txt（非TLS端口）。

为什么用 Globalping，而不是直接在本脚本里连：
    Cloudflare 是任播(anycast)，同一个 IP 从不同网络接入会落到不同机房。
    GitHub 官方 runner 全部在境外（实测 colo 多为 IAD），测出来的落点对中国用户毫无意义；
    数据源标注的机房则是它自己测试机的结果，也不代表你。
    Globalping 在国内有 50+ 个在线探针（移动/联通/电信/阿里/腾讯），
    通过它的 API 就能让"从中国实测"这一步也留在 GitHub Actions 里完成，无需本地操作。

为什么要同时测移动和联通：
    落地机房是按接入网络分流的，移动和联通经常落在不同机房。实测同一个 IP：
        104.20.30.105  中国移动(广州)=LAX  中国联通(南宁)=HKG
    只测一家，另一家的用户就会拿到落在香港的 IP。所以两家都要测，取交集才安全。

实测一致性：
    每家运营商先用一次种子测量找到一台在线探针，把它记成"移动=广州/AS9808"这样的固定坐标，
    之后所有候选 IP 都只用 country+asn+city+network 去点名这台探针，并且逐条校验测量结果里
    返回的探针身份和锁定的一致。不一致就说明 Globalping 挑了别的探针，这次结果作废。
    否则每个 IP 随机落到不同城市/运营商的探针上，落点不可比，筛选结果没有意义。

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

# 探针分组：每个分组锁定一台固定探针，同一个 IP 由所有分组的探针同时实测。
# 9808 / 56046 是中国移动骨干，其探针多为 eyeball-network（家宽），最接近家用宽带的落点；
# 联通同理。某天某家探针全都不在线时该组自动跳过，本次只按还在线的那家过滤。
PROBE_GROUPS = [
    {'label': '移动', 'asns': [9808, 56046, 56041, 24400, 24445]},
    {'label': '联通', 'asns': [4837, 17621, 9929]},
]
PROBE_FALLBACK = {'country': 'CN'}       # 上面都没有就用全部国内探针

# 实测用的 Cloudflare 站点（须为 CF 上的域名，用它固定 SNI 去连候选IP）
MEASURE_HOST = 'www.cloudflare.com'
MEASURE_PATH = '/cdn-cgi/trace'

POLL_INTERVAL = 0.6      # 官方要求两次轮询间隔 >= 0.5s，否则触发 2 req/s 限流
POLL_MAX_TIMES = 40      # 最多轮询次数（约 24 秒）
HTTP_TIMEOUT = 20

# 锁定探针后，用这几个字段去点名同一台探针（API 不接受多个"按测量id复用探针"的地点，
# 但可以一次传多个 country+asn+city+network 点名，所以移动/联通能在同一次测量里同时实测）
PROBE_PIN_FIELDS = ('country', 'asn', 'city', 'network')

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


def probe_key(probe):
    """探针身份，用于把测量结果对回是哪一台探针（API 返回的探针摘要里没有 id）。"""
    return (probe.get('asn'), probe.get('city'), probe.get('network'))


def describe(probe):
    return f"{probe.get('city', '?')}/{probe.get('network', '?')}(AS{probe.get('asn', '?')})"


def discover_probe(group, used):
    """
    为一个分组锁定一台在线探针：先用一次种子测量找到它，再把它的坐标记下来。
    返回 {'label','location','asn','city','network'}；没有在线探针时返回 None。
    """
    label = group['label']
    attempts = [{'country': 'CN', 'asn': asn} for asn in group['asns']] + [PROBE_FALLBACK]
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
            print(f'  {label} {who} 建测量失败: {err}')
            continue

        results = data.get('results') or []
        if not results:
            print(f'  {label} {who} 暂时没有在线探针，换下一个运营商')
            continue

        probe = results[0].get('probe') or {}
        identity = probe_key(probe)
        if identity in used:
            # 回退到"国内任意探针"时可能挑到别组已锁定的机器，重复测没有意义
            print(f'  {label} {who} 与已锁定的探针相同（{probe.get("network", "?")}），跳过')
            continue

        pin = {field: probe.get(field) for field in PROBE_PIN_FIELDS}
        found = {'label': label, 'location': pin}
        found.update(probe)
        print(f'  {label} 已锁定探针: {describe(probe)}')
        return found

    return None


def lock_probes():
    """每个分组各锁定一台探针，返回按分组顺序排列的探针列表。"""
    locked = []
    used = set()
    for group in PROBE_GROUPS:
        probe = discover_probe(group, used)
        if probe is None:
            print(f'  {group["label"]} 暂时没有在线探针，本次不参与过滤')
            continue
        used.add(probe_key(probe))
        locked.append(probe)

    if not locked:
        raise RuntimeError('Globalping 目前没有可用的中国大陆探针')
    if len(locked) < len(PROBE_GROUPS):
        print('注意：本次只拿到部分运营商的探针，筛选结果仅对这些运营商有效。')
    return locked


def measure_colos(probes, ip):
    """
    同一次测量里让所有锁定的探针各测一次该 IP。
    返回 {分组label: colo}；没测出机房、或回来的探针不是锁定的那台，都不会出现在结果里，
    由调用方重新锁定探针后重试。
    """
    payload = {
        'type': 'http',
        'target': ip,
        'locations': [probe['location'] for probe in probes],
        'measurementOptions': {
            'protocol': 'HTTPS',
            'port': 443,
            'request': {'host': MEASURE_HOST, 'path': MEASURE_PATH, 'method': 'GET'},
        },
    }
    created = create_measurement(payload)
    data = wait_measurement(created['id'])
    if data.get('status') != 'finished':
        return {}

    label_by_probe = {probe_key(probe): probe['label'] for probe in probes}
    colos = {}
    for item in data.get('results') or []:
        # 探针身份对不上，说明这次挑了别的探针，结果不可比，丢掉
        label = label_by_probe.get(probe_key(item.get('probe') or {}))
        result = item.get('result') or {}
        if not label or result.get('status') != 'finished':
            continue
        body = result.get('rawBody') or result.get('rawOutput') or ''
        colo = re.search(r'^colo=(\S+)', body, re.M)
        if colo:
            colos[label] = colo.group(1)
    return colos


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

want_labels = '+'.join(group['label'] for group in PROBE_GROUPS)
print(f'准备从中国大陆探针实测 {len(ips)} 个IP的落地机房（{want_labels} 同时实测，取交集）...')
try:
    probes = lock_probes()
except RuntimeError as err:
    print(f'锁定探针失败: {err}')
    print('未生成 ip.txt / notslip.txt（已有文件保持不变）。')
    raise SystemExit(1)

def reseed():
    """重新锁定所有探针；锁定不了就直接退出，避免写出错误的文件。"""
    global probes
    before = [probe['label'] for probe in probes]
    try:
        probes = lock_probes()
    except RuntimeError as err:
        print(f'重新锁定探针失败: {err}')
        print('未生成文件（已有文件保持不变）。')
        raise SystemExit(1)
    if [probe['label'] for probe in probes] != before:
        print(f'注意：参与过滤的运营商变成了 {"、".join(probe["label"] for probe in probes)}，'
              '本次结果的 #后缀以最后一次锁定的探针顺序为准。')


def measure_from_china(ip):
    """
    实测单个IP，返回与 probes 同顺序的机房列表。
    所有探针都测出机房才返回，缺一边就返回 None（拿不准的一律不放行）。
    有探针掉线或挑错探针时，重新锁定后重试一次。
    """
    for attempt in range(2):
        try:
            colos = measure_colos(probes, ip)
        except RuntimeError as err:
            # 建不出测量一般是限流或 API 挂了，继续跑只会把剩下的 IP 一路错杀，直接退出
            print(f'  {ip} 无法创建测量: {err}')
            print('未生成文件（已有文件保持不变）。')
            raise SystemExit(1)

        if len(colos) == len(probes):
            return [colos[probe['label']] for probe in probes]

        if attempt == 0:
            print(f'  {ip} 只有 {len(colos)}/{len(probes)} 台探针返回结果，重新锁定后重试...')
            reseed()
            continue
        return None

    return None


kept = []
colo_map = {}          # ip -> [移动的colo, 联通的colo]
hkg_ips = []
failed_ips = []

for index, ip in enumerate(ips, 1):
    colos = measure_from_china(ip)

    if colos is None:
        failed_ips.append(ip)
        print(f'  [{index}/{len(ips)}] {ip:<16} 实测失败')
    else:
        detail = '  '.join(f'{probe["label"]}={colo}' for probe, colo in zip(probes, colos))
        if any(colo.upper() in EXCLUDE_DATACENTERS for colo in colos):
            hkg_ips.append(ip)
            mark = '  <-- 已排除'
        else:
            kept.append(ip)
            colo_map[ip] = colos
            mark = ''
        print(f'  [{index}/{len(ips)}] {ip:<16} {detail}{mark}')

labels = list(dict.fromkeys(probe['label'] for probe in probes))
probe_desc = '、'.join(probe['label'] + '=' + describe(probe) for probe in probes)
label_suffix = ','.join(probe['label'] for probe in probes)
exclude_name = '/'.join(sorted(EXCLUDE_DATACENTERS))
print(f'实测成功 {len(kept) + len(hkg_ips)}/{len(ips)} 个（探针：{probe_desc}）')
if hkg_ips:
    print(f'{" 或 ".join(labels)}实测落在 {exclude_name} 已排除 {len(hkg_ips)} 个: {", ".join(hkg_ips)}')
if failed_ips:
    # 有一边没测出来（连不上或读不到机房）的，一并剔除，避免漏掉香港
    print(f'实测失败已排除 {len(failed_ips)} 个: {", ".join(failed_ips)}')

if not kept:
    # 全部失败通常是探针或 API 出了问题，此时不覆盖已有的 ip.txt / notslip.txt
    print('过滤后没有可用IP，未生成文件（已有文件保持不变）。')
    raise SystemExit(1)

# ---------------------------------------------------------------- 写文件

with open('ip.txt', 'w', encoding='utf-8') as file:
    for ip in kept:
        file.write(f"{ip}:{random.choice(tsl_ports)}#{','.join(colo_map[ip])}\n")

with open('notslip.txt', 'w', encoding='utf-8') as file:
    for ip in kept:
        file.write(f"{ip}:{random.choice(notsl_ports)}#{','.join(colo_map[ip])}\n")

print(f'已生成 ip.txt / notslip.txt，共 {len(kept)} 个IP'
      f'（{" 和 ".join(labels)}都确认非{exclude_name}，#后面按 {label_suffix} 顺序列出实测机房）')