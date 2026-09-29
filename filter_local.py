"""
本地实测过滤脚本：在你自己的网络环境执行。

读取 collect_ips.py 抓取的 candidates.txt，逐个IP直连实测Cloudflare真实落地的机场码，
排除香港(HKG)等指定机房后，生成 ip.txt（TLS端口）和 notslip.txt（非TLS端口）。

为什么必须本地测：
    Cloudflare是任播(anycast)，同一个IP从不同网络接入会落到不同机房。
    数据源标注的机房是它测试机的结果，GitHub Actions 在境外跑，测出来的落点同样不代表你。
    只有在你自己的线路上实测，得到的 colo 才是你真实的落点。

用法：
    python filter_local.py
"""
import os
import re
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor

# 候选文件，由 collect_ips.py 生成
CANDIDATES_FILE = 'candidates.txt'

# 需要排除的机房代码（IATA），香港=HKG，可按需追加
EXCLUDE_DATACENTERS = {'HKG'}

# 实测并发数与单个超时（秒），依赖系统自带的curl命令
MEASURE_THREADS = 8
MEASURE_TIMEOUT = 5
# 实测用的Cloudflare站点（须为CF上的域名，用它固定SNI去连候选IP）
MEASURE_HOST = 'www.cloudflare.com'

# TLS端口（写入ip.txt）
tsl_ports = ["443", "8443", "2053", "2083", "2087", "2096"]
# 非TLS端口（写入notslip.txt）
notsl_ports = ["80", "8080", "8880", "2052", "2082", "2086", "2095"]

ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'


def measure_colo(ip):
    """直连指定IP发起HTTPS请求，返回Cloudflare实际落地的机场码（如HKG/LAX），失败返回None"""
    host = MEASURE_HOST
    cmd = ['curl', '-s', '--max-time', str(MEASURE_TIMEOUT),
           '--resolve', f'{host}:443:{ip}', f'https://{host}/cdn-cgi/trace']
    run_kwargs = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
                  'timeout': MEASURE_TIMEOUT + 3}
    if os.name == 'nt':
        # Windows下不弹出控制台窗口
        run_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        proc = subprocess.run(cmd, **run_kwargs)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.decode('utf-8', 'ignore')
    for line in text.splitlines():
        if line.startswith('colo='):
            colo = line.split('=', 1)[1].strip()
            return colo or None
    return None


# 读取候选IP（格式：IP#机房，兼容带端口的写法）
if not os.path.exists(CANDIDATES_FILE):
    print(f'未找到 {CANDIDATES_FILE}，请先运行 collect_ips.py 抓取候选。')
    raise SystemExit(1)

candidates = []
with open(CANDIDATES_FILE, encoding='utf-8') as file:
    for line in file:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.search(ip_pattern, line)
        if m:
            candidates.append(m.group(0))

# 去重并保持原有顺序
ips = list(dict.fromkeys(candidates))
if not ips:
    print(f'{CANDIDATES_FILE} 里没有可用的IP。')
    raise SystemExit(1)

# 本地实测每个IP的真实落地机房
print(f'开始本地实测 {len(ips)} 个IP的落地机房（超时{MEASURE_TIMEOUT}秒）...')
with ThreadPoolExecutor(max_workers=min(MEASURE_THREADS, len(ips))) as pool:
    colos = list(pool.map(measure_colo, ips))

kept = []
colo_map = {}
hk_ips = []
failed_ips = []
for ip, colo in zip(ips, colos):
    if not colo:
        failed_ips.append(ip)
    elif colo.upper() in EXCLUDE_DATACENTERS:
        hk_ips.append(ip)
    else:
        kept.append(ip)
        colo_map[ip] = colo

print(f'实测成功 {len(kept) + len(hk_ips)}/{len(ips)} 个')
if hk_ips:
    print(f'本地实测为香港(HKG)已排除 {len(hk_ips)} 个: {", ".join(hk_ips)}')
if failed_ips:
    # 连不上或无法确认机房的，一并剔除，避免漏掉香港
    print(f'实测失败已排除 {len(failed_ips)} 个: {", ".join(failed_ips)}')

if not kept:
    # 全部失败通常是网络环境问题，此时不覆盖已有的 ip.txt / notslip.txt
    print('过滤后没有可用IP，未生成文件（已有文件保持不变）。')
    raise SystemExit(1)

# 写入TLS端口版本
with open('ip.txt', 'w', encoding='utf-8') as file:
    for ip in kept:
        file.write(f"{ip}:{random.choice(tsl_ports)}#{colo_map[ip]}\n")

# 写入非TLS端口版本
with open('notslip.txt', 'w', encoding='utf-8') as file:
    for ip in kept:
        file.write(f"{ip}:{random.choice(notsl_ports)}#{colo_map[ip]}\n")

print(f'已生成 ip.txt / notslip.txt，共 {len(kept)} 个非香港IP')
print('如需同步到仓库，请执行：git add ip.txt notslip.txt && git commit && git push')
