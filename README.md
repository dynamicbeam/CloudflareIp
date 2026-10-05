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
`filter_globalping.py` 用**移动、联通两台国内探针同时实测**，取两边都不在香港的交集。

### candidates.txt
全量候选，格式 `IP#数据源机房`，只做参考，**不做过滤**：
```
104.17.149.180#SJC
104.18.77.124#LAX
```

### 为什么要同时测移动和联通
Cloudflare 是任播（anycast），同一个 IP 从不同网络接入会落到不同机房：

- 数据源标注的机房，是**它测试机**的落点，不代表你的线路
- GitHub 官方 runner 全部在境外（实测 colo 多为 `IAD`），测出来的落点对中国用户毫无意义
- 只有从**中国大陆网络**实测 `colo`，才是有意义的落点

而且落地机房还**按运营商分流**：同一个 IP，移动和联通经常落在不同机房。
只测一家，另一家的用户就会拿到落在香港的 IP —— 所以两家都要测，取交集。

实测例子（同一批 IP，三个地方四个答案）：

| IP | 数据源测试机 | **中国移动(广州)** | **中国联通(南宁)** | GitHub 官方 runner | 结论 |
| --- | --- | --- | --- | --- | --- |
| 104.16.151.225 | HKG | **HKG** | LAX | IAD | 排除（移动落香港） |
| 104.20.30.105 | FRA | LAX | **HKG** | IAD | 排除（联通落香港） |
| 162.159.147.222 | FRA | LHR | **HKG** | IAD | 排除（联通落香港） |
| 104.17.230.127 | LAX | **SEA** | **LAX** | IAD | 保留 |
| 172.67.237.18 | AMS | **LAX** | **LAX** | IAD | 保留 |

后两行就是实测出来的可用 IP。第 2、3 行只要漏测联通，就会被当成好 IP 发出去。

`filter_globalping.py` 借助 [Globalping](https://globalping.io) 的 API，把"从中国实测"这一步也留在 Actions 内完成：

1. 每家运营商先用一次种子测量**各锁定一台探针**（移动优先 `AS9808`、联通优先 `AS4837`，都是家宽探针，最接近家用宽带；没有就换同家的其他 AS，最后才回退到国内任意探针）
2. 锁定后用 `country+asn+city+network` 点名同一台探针，所有候选 IP 都在**同一次测量里由移动、联通两台探针同时实测**，并逐条校验返回的探针身份和锁定的一致，保证落点可比
3. 探针用 `https` 连候选 IP 的 443 端口，但 SNI/Host 固定为 `www.cloudflare.com`，读 `/cdn-cgi/trace` 里的真实 `colo`
4. **任意一边**命中 `EXCLUDE_DATACENTERS` 就剔除；有**任意一边**没测出来（连不上或读不到机房）也剔除，宁缺毋滥

某天某家的探针全都不在线时，该家自动跳过，本次只按还在线的那家过滤，并在日志里提示。
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
`ip.txt` 与 `notslip.txt` 每行格式为 `IP:端口#移动落地机房,联通落地机房`，
后缀是**移动、联通两台探针分别实测**的机房代码，按 `#` 后逗号顺序一一对应：
```
104.17.230.127:443#SEA,LAX
172.67.237.18:8443#LAX,LAX
```
写成两个机房是为了排查方便：直接就能看出这条 IP 是移动、联通都测过才放行的。
某天只有一家探针在线时，`#` 后面就只有那一个机房。

## 排除香港IP
数据源表格中的「数据中心」列标注了每个IP实测落地的机房，但那是测试机的结果。
`filter_globalping.py` 以**中国探针实测**为准，**移动或联通任意一边**实测机房命中
`EXCLUDE_DATACENTERS` 的 IP，都不写入 `ip.txt` 和 `notslip.txt`；
有一边实测失败（连不上 / 读不到机房）的也一并剔除。
如需调整排除哪些机房，或要测哪几家运营商，在脚本顶部修改：
```python
EXCLUDE_DATACENTERS = {'HKG'}
PROBE_GROUPS = [
    {'label': '移动', 'asns': [9808, 56046, 56041, 24400, 24445]},
    {'label': '联通', 'asns': [4837, 17621, 9929]},
]
```
