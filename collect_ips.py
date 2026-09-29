import requests
from bs4 import BeautifulSoup
import re
import os

# 目标URL列表
urls = [
    'https://www.wetest.vip/page/cloudflare/address_v4.html'
]

# 正则表达式用于匹配IP地址
ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'

# 候选文件：全量抓取结果，此处不做机房过滤。
# Cloudflare是任播(anycast)，数据源标注的机房只代表它测试机的落点，
# 真正的落地机房必须在自己的线路上实测，所以过滤交给 filter_local.py。
CANDIDATES_FILE = 'candidates.txt'

# 使用集合存储IP地址实现自动去重
unique_ips = set()

for url in urls:
    try:
        # 发送HTTP请求获取网页内容
        response = requests.get(url, timeout=5)

        # 确保请求成功
        if response.status_code == 200:
            # 使用正则表达式查找IP地址
            ip_matches = re.findall(ip_pattern, response.text, re.IGNORECASE)

            # 将找到的IP添加到集合中（自动去重）
            unique_ips.update(ip_matches)
    except requests.exceptions.RequestException as e:
        print(f'请求 {url} 失败: {e}')
        continue

# 将去重后的IP地址按数字顺序排序后写入候选文件
if unique_ips:
    # 按IP地址的数字顺序排序（非字符串顺序）
    sorted_ips = sorted(unique_ips, key=lambda ip: [int(part) for part in ip.split('.')])

    # 存储IP和数据中心的对应关系
    ip_to_dc_map = {}
    # 重新请求网页获取完整内容，包括数据中心信息
    try:
        response = requests.get(urls[0], timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # 查找表格行
            rows = soup.find_all('tr')
            # 遍历表格行查找数据中心信息
            for row in rows:
                # 通过data-label定位列，避免依赖列顺序
                ip_cell = row.find('td', attrs={'data-label': '优选地址'})
                dc_cell = row.find('td', attrs={'data-label': '数据中心'})
                if ip_cell:
                    # 从单元格文本中提取IP地址
                    ip_match = re.search(ip_pattern, ip_cell.get_text())
                    if ip_match:
                        ip = ip_match.group(0)
                        ip_to_dc_map[ip] = dc_cell.get_text(strip=True) if dc_cell else '未知'
    except Exception as e:
        print(f'解析数据中心信息失败: {e}')

    # 写入候选文件，格式：IP#数据源机房（机房仅供参考，不代表本地落点）
    with open(CANDIDATES_FILE, 'w', encoding='utf-8') as file:
        for ip in sorted_ips:
            file.write(f"{ip}#{ip_to_dc_map.get(ip, '未知')}\n")
    print(f'已写入 {len(sorted_ips)} 个候选IP到 {CANDIDATES_FILE}')
    print('机房过滤需在本地线路执行：python filter_local.py')
else:
    # 抓取失败时不删除已有文件，避免误提交删除
    print('未找到有效的IP地址，保留已有候选文件。')
