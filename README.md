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

## 抓取与过滤的分工

| 步骤 | 脚本 | 在哪跑 | 产物 |
| --- | --- | --- | --- |
| 抓取候选 | `collect_ips.py` | GitHub Actions（每天 00:16） | `candidates.txt` |
| 实测过滤 | `filter_local.py` | **你本地** | `ip.txt`、`notslip.txt` |

### candidates.txt
全量候选，格式 `IP#数据源机房`，只做参考，**不做过滤**：
```
104.17.149.180#SJC
104.18.77.124#LAX
```

### 为什么过滤必须在本地跑
Cloudflare 是任播（anycast），同一个 IP 从不同网络接入会落到不同机房：

- 数据源标注的机房，是**它测试机**的落点，不代表你的线路
- GitHub Actions 在境外跑，实测出的落点同样不代表你
- 只有在自己的线路上实测 `colo`，才是你真实的落地机房

所以 `filter_local.py` 会用 curl 指定候选 IP 直连 `www.cloudflare.com/cdn-cgi/trace`，读取真实 `colo`，只保留非香港的 IP。实测失败（连不上）的也会剔除，避免漏掉香港。

### 用法
```bash
python collect_ips.py     # 可选，直接抓取最新候选（不跑 Actions 时用）
python filter_local.py    # 本地实测并生成 ip.txt / notslip.txt
git add ip.txt notslip.txt candidates.txt
git commit -m "本地实测过滤"
git push
```

`filter_local.py` 顶部可调整排除的机房：
```python
EXCLUDE_DATACENTERS = {'HKG'}
```

注意：实测时请确保**直连**（关掉 TUN 模式 / 系统代理），否则测出来的是代理的落点。

## 输出格式
`ip.txt` 与 `notslip.txt` 每行格式为 `IP:端口#实际落地机房`，后缀是**本地实测**的机房代码（如 `LAX`、`AMS`、`FRA`）：
```
104.18.124.45:8443#LAX
104.26.4.232:2053#AMS
```

## 排除香港IP
数据源表格中的「数据中心」列标注了每个IP实测落地的机房，但那是测试机的结果。
`filter_local.py` 以**本地实测**为准，实测机房命中 `EXCLUDE_DATACENTERS` 的 IP 不写入 `ip.txt` 和 `notslip.txt`。
如需调整，在脚本顶部修改：
```python
EXCLUDE_DATACENTERS = {'HKG'}
```
