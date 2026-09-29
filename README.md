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
| 抓取候选 | `collect_ips.py` | GitHub Actions | `candidates.txt` |
| 实测过滤 | `filter_globalping.py` | GitHub Actions | `ip.txt`、`notslip.txt` |
| 实测过滤（备用） | `filter_local.py` | 你本地（可选核对用） | `ip.txt`、`notslip.txt` |

整条链路都在 GitHub Actions 里跑完，不需要本地做任何操作。

### candidates.txt
全量候选，格式 `IP#数据源机房`，只做参考，**不做过滤**：
```
104.17.149.180#SJC
104.18.77.124#LAX
```

### 为什么要从中国实测
Cloudflare 是任播（anycast），同一个 IP 从不同网络接入会落到不同机房：

- 数据源标注的机房，是**它测试机**的落点，不代表你的线路
- GitHub 官方 runner 全部在境外（实测 colo 多为 `IAD`），测出来的落点对中国用户毫无意义
- 只有从**中国大陆网络**实测 `colo`，才是有意义的落点

`filter_globalping.py` 借助 [Globalping](https://globalping.io) 的 API，把"从中国实测"这一步也留在 Actions 内完成：

1. 先建一次种子测量，**锁定一个中国大陆探针**（优先中国移动 `AS9808`，家宽探针最接近家用宽带；依次回退联通/电信/国内任意）
2. 所有候选 IP 都交给这**同一个探针**去测，保证落点可比
3. 探针用 `https` 连候选 IP 的 443 端口，但 SNI/Host 固定为 `www.cloudflare.com`，读 `/cdn-cgi/trace` 里的真实 `colo`
4. `colo` 命中 `EXCLUDE_DATACENTERS` 的剔除；实测失败的也剔除，避免漏掉香港

实测例子（同一批 IP，三个地方三个答案）：

| IP | 数据源测试机 | **中国探针实测** | GitHub 官方 runner |
| --- | --- | --- | --- |
| 104.17.106.239 | LAX | **HKG** | IAD |
| 104.20.6.18 | FRA | **DFW** | IAD |
| 162.159.136.223 | FRA | **LHR** | IAD |

实测值与本地家宽用 curl 测出来的结果逐条一致。

### 不用配任何东西
Actions 每天自动完成，无需本地操作。Globalping 未认证时按出口 IP 限流（250 次/小时），
本项目每次运行只消耗十几次，够用；如果哪天被限流，去 <https://dash.globalping.io/tokens>
申请一个免费令牌，作为仓库 secret 命名为 `GLOBALPING_TOKEN` 即可。

### 可选：本地核对
想确认线上结果和自己看到的一致，可以本地跑一遍（需要 curl，Windows 自带）：
```bash
python collect_ips.py     # 可选，直接抓取最新候选（不跑 Actions 时用）
python filter_local.py    # 本地实测，用于和 Action 结果交叉验证
```
注意：实测时请确保**直连**（关掉 TUN 模式 / 系统代理），否则测出来的是代理的落点。

## 输出格式
`ip.txt` 与 `notslip.txt` 每行格式为 `IP:端口#实测落地机房`，后缀是**从中国探针实测**的机房代码（如 `DFW`、`LHR`、`SJC`）：
```
104.20.6.18:8443#DFW
162.159.136.223:2053#LHR
```

## 排除香港IP
数据源表格中的「数据中心」列标注了每个IP实测落地的机房，但那是测试机的结果。
`filter_globalping.py` 以**中国探针实测**为准，实测机房命中 `EXCLUDE_DATACENTERS` 的 IP 不写入 `ip.txt` 和 `notslip.txt`。
如需调整，在脚本顶部修改：
```python
EXCLUDE_DATACENTERS = {'HKG'}
```
