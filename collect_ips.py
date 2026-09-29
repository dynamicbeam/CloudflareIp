import requests
from bs4 import BeautifulSoup
import re
import os
import random

# 目标URL列表
urls = [
    'https://www.wetest.vip/page/cloudflare/address_v4.html'
]

# 正则表达式用于匹配IP地址
ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'

# 需要排除的数据中心代码（数据源「数据中心」列的IATA代码），香港=HKG，可按需追加
EXCLUDE_DATACENTERS = {'HKG'}

# 检查ip.txt文件是否存在,如果存在则删除它
if os.path.exists('ip.txt'):
    os.remove('ip.txt')

# 使用集合存储IP地址实现自动去重
unique_ips = set()

for url in urls:
    try:
        # 发送HTTP请求获取网页内容
        response = requests.get(url, timeout=5)
        
        # 确保请求成功
        if response.status_code == 200:
            # 获取网页的文本内容
            html_content = response.text
            
            # 使用正则表达式查找IP地址
            ip_matches = re.findall(ip_pattern, html_content, re.IGNORECASE)
            
            # 将找到的IP添加到集合中（自动去重）
            unique_ips.update(ip_matches)
    except requests.exceptions.RequestException as e:
        print(f'请求 {url} 失败: {e}')
        continue

# 将去重后的IP地址按数字顺序排序
if unique_ips:
    # 按IP地址的数字顺序排序（非字符串顺序）
    sorted_ips = sorted(unique_ips, key=lambda ip: [int(part) for part in ip.split('.')])
    
    # 定义不同运营商的端口列表
    tsl_ports = ["443", "8443", "2053", "2083", "2087", "2096"]
    
    # 定义notslip.txt使用的端口列表
    notsl_ports = ["80", "8080", "8880", "2052", "2082", "2086", "2095"]
    
    # 创建结果字符串
    result = []
    
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
        # 如果解析失败，给所有IP分配未知数据中心
        for ip in sorted_ips:
            ip_to_dc_map[ip] = '未知'
    # 排除指定数据中心（香港HKG）的IP，数据中心未知的IP予以保留
    excluded_ips = [ip for ip in sorted_ips
                    if ip_to_dc_map.get(ip, '未知').upper() in EXCLUDE_DATACENTERS]
    if excluded_ips:
        sorted_ips = [ip for ip in sorted_ips
                      if ip_to_dc_map.get(ip, '未知').upper() not in EXCLUDE_DATACENTERS]
        print(f'已排除 {len(excluded_ips)} 个香港(HKG)机房IP: {", ".join(excluded_ips)}')
    # 缺少数据中心信息的IP无法判断归属，保留并提示，便于发现数据源结构变化
    unknown_dc = [ip for ip in sorted_ips if ip_to_dc_map.get(ip, '未知') == '未知']
    if unknown_dc:
        print(f'注意：{len(unknown_dc)} 个IP缺少数据中心信息，未做过滤: {", ".join(unknown_dc)}')
    if not sorted_ips:
        print('排除香港机房后没有剩余的IP地址，请检查 EXCLUDE_DATACENTERS 设置。')

    # 为每个IP地址随机选择一个端口，并添加数据中心信息
    for ip in sorted_ips:
        random_port = random.choice(tsl_ports)
        dc = ip_to_dc_map.get(ip, '未知')
        result.append(f"{ip}:{random_port}#{dc}")
    # 写入文件
    with open('ip.txt', 'w', encoding='utf-8') as file:
        for line in result:
            file.write(line + '\n')
    # 创建notslip.txt文件内容
    notslip_result = []
    # 为每个IP地址随机选择一个notsl端口，并添加数据中心信息
    for ip in sorted_ips:
        random_port = random.choice(notsl_ports)
        dc = ip_to_dc_map.get(ip, '未知')
        notslip_result.append(f"{ip}:{random_port}#{dc}")
    # 检查notslip.txt文件是否存在，如果存在则删除它
    if os.path.exists('notslip.txt'):
        os.remove('notslip.txt')
    # 写入notslip.txt文件
    with open('notslip.txt', 'w', encoding='utf-8') as file:
        for line in notslip_result:
            file.write(line + '\n')
    

    
    
else:
    print('未找到有效的IP地址。')
