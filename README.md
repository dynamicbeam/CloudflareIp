每天自动抓取
```
https://www.wetest.vip/page/cloudflare/address_v4.html
```
和
```
 https://ip.164746.xyz
```
```
 https://cf.090227.xyz
```
```
https://stock.hostmonit.com/CloudFlareYes
```
的优选ip，形成ip.txt 


## 修改
每天从`https://www.wetest.vip/page/cloudflare/address_v4.html`抓取，并拼接端口

## 输出格式
`ip.txt` 与 `notslip.txt` 每行格式为 `IP:端口#数据中心`，后缀为数据源「数据中心」列的机房代码（如 `LAX`、`AMS`、`FRA`），不再使用线路名称（移动/联通/电信）：
```
104.18.124.45:8443#LAX
104.26.4.232:2053#AMS
```

## 排除香港IP
数据源表格中的「数据中心」列标注了每个IP实测落地的机房，`collect_ips.py` 会过滤掉 `HKG`（香港）的IP后再写入 `ip.txt` 和 `notslip.txt`。
如需调整，在脚本顶部修改：
```python
EXCLUDE_DATACENTERS = {'HKG'}
```
