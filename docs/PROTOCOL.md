# 本校校园网认证协议（实测记录）

> 实测时间：2026-10　环境：Dr.COM 城市热点（城市热点 / DrcomServer）
> 目的：记录清楚这所学校用的是哪套接口，方便日后学校升级后快速定位问题，
> 也方便别的学校同学对照自己学校是否一致。

## 1. 门户首页（80 端口）

```http
GET http://172.30.255.42/ HTTP/1.1
```

```http
HTTP/1.1 200 OK
Server: DrcomServer1.0
Content-Type: text/html; charset=gbk
```

返回的 HTML 不是静态登录表单，而是一个由 `a41.js` 渲染的哆点（Dr.COM 客户端）页面，
参数通过内联 JS 变量下发：

```javascript
// 结构示意（真实值已隐去）
uid='<用户ID>'; pwd=''; v4ip='<本机IP>';
stime='<开始时间>'; etime='<结束时间>'; time='<在线秒数>'; flow='<流量>';

portalver='4.0';
authtype=1;
authloginport=801;
authloginpath='/eportal/?c=ACSetting&a=Login';
authuserfield='DDDDD';
authpassfield='upass';
authsuccess='Dr.COMWebLoginID_3.htm';
authfail='Dr.COMWebLoginID_2.htm';
charset='gb2312';
carrier='{"yys":{...}}';   // 服务类型：校园用户 / 校园电信(@dx) / 校园联通(@lt) / 校园其他
```

页面里 `authloginpath` 指向的是**老版** `ACSetting` 表单接口。但实测该接口已经下线（见第 4 节），
真正可用的是 801 端口的新版 JSONP 接口。

## 2. 登录（新版接口，实际使用）

```http
GET http://172.30.255.42:801/eportal/portal/login?callback=dr1003&login_method=1
      &user_account=<账号>&user_password=<密码>
      &wlan_user_ip=<本机出口IP>&wlan_user_ipv6=&wlan_user_mac=000000000000
      &wlan_ac_ip=&wlan_ac_name=&jsVersion=4.2.1&terminal_type=1&lang=zh-cn&v=5911
```

| 参数 | 说明 |
| --- | --- |
| `callback` | JSONP 回调名，固定 `dr1003` |
| `login_method` | 固定 `1` |
| `user_account` | 账号，明文，需 URL 编码；部分部署要求写成 `,0,学号`（编码后是 `%2C0%2C学号`）；需要运营商后缀时拼在账号后面（如 `123456@dx`） |
| `user_password` | 密码，**明文**（URL 编码），不是 base64/MD5 |
| `wlan_user_ip` | 本机在校园网内的出口 IP，必须正确，否则认证到别的 IP 上 |
| `wlan_user_mac` | 全零即可 |
| `jsVersion` | 门户脚本版本，服务端只做兼容性检查 |
| `v` | 缓存穿透参数，服务端不校验取值 |

响应（`Content-Type: application/javascript`）：

```javascript
dr1003({"result":1,"msg":"...","ret_code":0});   // 成功
dr1003({"result":0,"msg":"IP: x.x.x.x 已经在线！","ret_code":2}); // 已经在线
dr1003({"result":0,"msg":"账号或密码错误","ret_code":1});        // 失败
```

判定：`result == 1` 成功；`ret_code == 2` 视为已在线（也算成功）。

### 2.1 账号格式由门户下发：`account_prefix`

同一个接口在不同学校有两种账号写法，**不要猜，问门户**：

| 写法 | 例子 | 适用条件 |
| --- | --- | --- |
| 纯账号 | `user_account=123456` | 门户下发 `account_prefix != "1"` |
| 带前缀 | `user_account=%2C0%2C123456`（即 `,0,123456`） | 门户下发 `account_prefix == "1"`（**本校属于这种**） |

判断依据来自下一节的 `loadConfig` 接口，本校实测（已脱敏）：

```json
dr1003({"code":1,"msg":"加载页面设置信息成功！","data":{
  "account_prefix":"1",
  "account_suffix":"",
  "login_method":"1",
  "ep_http_port":"801",
  ...
}});
```

即：**账号必须写成 `,0,学号`，且不需要运营商后缀。**

为什么不能靠“试”来发现：认证服务器先按源 IP 判断会话，已在线时直接返回
`"IP: x.x.x.x 已经在线！"`，根本不会校验账号密码 —— 纯账号和带前缀都会“成功”。
所以只有离线时才能验证，或者直接读 `loadConfig`。本工具选后者。

> 注意：短时间内反复请求 `/eportal/portal/login` 会触发 nginx 的 503 限流，
> 调试时别连续刷。

## 2.2 门户页面配置：`/eportal/portal/page/loadConfig`

登录前浏览器会先拉这份配置，里面决定了账号格式、登录方式、端口等：

```http
GET http://172.30.255.42:801/eportal/portal/page/loadConfig
    ?program_index=&wlan_vlan_id=&wlan_user_ip=<base64(本机IP)>
    &wlan_user_ipv6=&wlan_user_ssid=&wlan_user_areaid=
    &wlan_ac_ip=&wlan_ap_mac=&gw_id=&callback=dr1003&jsVersion=4.X
```

| 字段 | 含义 |
| --- | --- |
| `account_prefix` | `"1"` 表示账号要加 `,0,` 前缀 |
| `account_suffix` | 运营商后缀，如 `@telecom`；本校为空 |
| `login_method` | 登录方式，本校 `1` |
| `ep_http_port` | 认证端口，本校 `801` |
| `page_name` | 门户模板名（本校模板名以 `szu` 开头，和深大同源） |

注意 `wlan_user_ip` 是 **base64 编码**的 IP，不是明文。

## 3. 在线状态（80 端口）

```http
GET http://172.30.255.42/drcom/chkstatus?callback=dr1003
```

```javascript
dr1003({"result":1,"v4ip":"<本机IP>","stime":"<开始时间>","etime":"<结束时间>",
        "time":"<在线秒数>","flow":"<累计流量>","uid":"<用户ID>",
        "ss5":"<本机IP>","ss6":"172.30.255.42", ...});
```

注意：该接口 **GBK 编码**，且是**按请求源 IP** 返回当前会话，传别的 IP 查询无效。
`result == 1` 表示在线。

## 4. 老接口的实际情况

| 接口 | 实测结果 |
| --- | --- |
| `POST :801/eportal/?c=ACSetting&a=Login` | 返回 801 端口的 Vue 管理后台 SPA（nginx），**不再是**认证表单接口 |
| `GET http://172.30.255.42/eportal/portal/login`（80 端口） | 404，该接口只在 801 端口 |
| `GET :801/eportal/portal/login` | ✔ 正常（第 2 节） |
| `GET /drcom/chkstatus` | ✔ 正常（第 3 节） |

所以页面里残留的 `ACSetting / DDDDD / upass` 只是兼容层痕迹，**不能**按老教程去 POST 表单，
否则会拿到一张 HTML 管理页而不是 `Msg=..;msga='..'` 的认证结果。

## 5. 注销

```http
GET http://172.30.255.42:801/eportal/portal/logout?callback=dr1003&login_method=1
      &user_account=webui&wlan_user_ip=<本机IP>&wlan_user_mac=000000000000
```

本工具不主动注销（没必要，反而是副作用）。

## 6. 三个容易踩的坑

1. **认证服务器按源 IP 识别会话**，不是按请求里传的 `wlan_user_ip`。
   已经在线时，即使传了错误的账号密码，也只会返回 `"IP: x.x.x.x 已经在线！"`，
   **不会**校验凭据 —— 所以“已在线”状态下测不出密码对不对，必须等掉线后再验证。
2. 登录成功页/失败页在部分部署里靠 `Dr.COMWebLoginID_3.htm` / `Dr.COMWebLoginID_2.htm`
   两个字符串区分；本校这套走的是 JSONP，判断 JSON 字段即可。
3. **离线时不要信任“自动探测出来的门户地址”**：未认证时访问外网会被劫持，
   探测程序容易把被劫持的探测地址（如 `www.msftconnecttest.com`）当成门户，
   于是把登录请求打到 `www.msftconnecttest.com:803` 这种不存在的地方，
   表现为“卡很久然后失败”。本仓库已改为**配置里的门户地址优先**，并加了
   0.1 秒级的门户状态快速判断。
