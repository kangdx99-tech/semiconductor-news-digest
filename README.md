# 半导体新闻日报 · semiconductor-news-digest

每天早上（或每晚）自动帮你读完几十个半导体信息源，挑出真正重要的 8-12 条，
写成一份 10 分钟能看完的中文日报，并推送到飞书 / Telegram / 邮件 / 本地文件。

不依赖任何 Agent 框架，clone 下来填个 API key 就能跑。

```
**2026-10-15 半导体新闻日报**

【一句话总结】
GlobalFoundries 获日本 210 亿美元融资在纽约新建两座晶圆厂，同时台积电即将发布
连续第六个季度创盈利新高的财报，但 ASML 强劲业绩未能阻止芯片股大跌 ……

【制造与代工】
① GlobalFoundries 获日本 210 亿美元融资，将在纽约新建两座晶圆厂
来源：cnBeta | 时间：2026-10-15
GlobalFoundries 宣布将在纽约州建设并运营两座新的半导体制造工厂，日本方面提供
210 亿美元融资。新厂面向汽车、工业控制、航空航天、国防以及人工智能等领域的需求。
🔗 cnBeta → https://www.cnbeta.com.tw/articles/tech/1582184.htm
```

完整示例见 [`examples/2026-10-15.md`](examples/2026-10-15.md)（由本仓库真实运行产物导出）。

---

## 它和"用 RSS 阅读器订阅"有什么区别

RSS 阅读器的活是**把文章推给你**；这个项目的活是**替你读、替你筛、替你写**。

| | RSS 阅读器 | 本项目 |
|---|---|---|
| 你要做的 | 自己扫几十条标题 | 什么都不用做 |
| 输出 | 原文列表 | 中文摘要 + 一句话总结 + 分主题 |
| 跨语言 | 英文原文自己读 | 英文源翻成中文，专有名词保留英文 |
| 死链 | 点进去才知道 | 写入前逐条校验，不可访问的直接丢弃 |
| 幻觉链接 | 不存在 | 白名单审计，模型编的 URL 一律丢弃 |

---

## 60 秒上手

```bash
git clone https://github.com/<you>/semiconductor-news-digest.git
cd semiconductor-news-digest

pip install -r requirements.txt

cp .env.example .env          # 填一个 API key
cp config.example.yaml config.yaml

python run.py --dry-run       # 先跑一次看效果，不推送任何地方
```

`--dry-run` 会把日报写到 `output/2026-xx-xx.md`，看完觉得合适再去配推送渠道。

**不想花钱配 API key？** 加 `--no-llm`，它会把校验通过的候选条目直接排版成列表
给你，不调用任何模型：

```bash
python run.py --no-llm --dry-run
```

---

## 环境要求

- Python 3.9+
- 依赖只有两个：`requests`、`PyYAML`（其他全走标准库）
- 一个大模型 API（任何 OpenAI 兼容接口：DeepSeek / OpenAI / OpenRouter / Kimi / 通义 / 本地 vLLM 都行）

---

## 它到底做了什么

```
  ┌─────────────┐
  │ 1. 采集     │  20 个 RSS/Atom 源 + 可选关键词搜索，并发抓取
  └──────┬──────┘
         │  时间窗过滤（默认过去 24h，周一自动放宽到 72h）
         │  标题归一化去重（同一事件的多家转载只留一条）
  ┌──────▼──────┐
  │ 2. 链接校验 │  并发 HEAD→GET，只留 200~399，403 算"可达但反爬"
  └──────┬──────┘
         │  ⚠️ 校验不通过的条目在这里就被删掉了，不会流到后面
         │  ⚠️ Google News 这类跳转壳链接被降级为"选题提示"，不进正文
  ┌──────▼──────┐
  │ 3. 大模型成稿│  候选条目 + 选题提示 → 中文日报 markdown
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │ 4. 链接审计 │  输出里的每个 URL 必须在候选白名单里，否则整块丢弃
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │ 5. 多渠道推送│  本地 md / 飞书云文档 + 聊天 / Telegram / 邮件
  └─────────────┘
```

### 三个关键设计

**1. 链接白名单审计（第 4 步）**

大模型编 URL 是这类工具最难发现的坑：链接长得完全正常，点进去 404 或者跑到别的站。
本项目在写入前做一次审计 —— 输出里出现的每个 URL 都必须在候选白名单里，
不在白名单的**整块内容直接丢弃**并记账（`output/run-*.json`）。

**2. 先校验，再写稿**

链接校验发生在喂给模型之前，所以模型根本看不到死链，也写不出死链。

**3. 飞书文档只增不删**

日报文档的价值在于历史积累。写入模块只用 `insert(index=0)`，
代码里没有调用任何 delete 接口，跑一百次也不会覆盖旧内容。

---

## 配置说明

### 大模型

```yaml
llm:
  base_url: https://api.deepseek.com/v1   # 换成你的服务商
  model: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY           # 到 .env 里找这个名字的变量
```

`.env`：

```
DEEPSEEK_API_KEY=sk-xxxxxxxx
```

一次运行大约消耗 8k 输入 / 1k 输出 token。以 DeepSeek 的价格算，**一天不到一分钱**。

### 采集源

`config.yaml` 里的 `sources` 就是普通的 RSS 地址，随便加：

```yaml
sources:
  - name: SemiEngineering
    url: https://semiengineering.com/feed/
```

仓库自带的源全部实测可用（更新于 2026-10）。想看当前配置了哪些：

```bash
python run.py --list-sources
```

**混合类源要标 `filter: true`**：综合科技媒体（IT之家、cnBeta、The Register…）
里半导体内容只占一小部分，不预过滤的话大模型会被带偏，写出
"苹果市值破 5 万亿""Grok Voice 发布"这种跟芯片无关的条目。
标上 `filter: true` 后，这个源只保留命中 `collect.keyword_filter` 关键词的条目；
垂直媒体的源不要标，它们本来就全是相关内容。

```yaml
  - name: IT之家
    url: https://www.ithome.com/rss/
    filter: true          # ← 混合类源

collect:
  keyword_filter: [半导体, 芯片, 晶圆, DRAM, HBM, TSMC, ...]   # 可自由增删
```

### 关键词搜索（可选）

```yaml
searches:
  - q: semiconductor chip news
    name: 搜索·英文半导体
```

⚠️ **Google News RSS 给的是跳转链接**（`news.google.com/rss/articles/...`），
点开是一层壳，不是原文。本项目的处理方式是：搜索结果**只把标题当选题提示**喂给模型，
链接永远不进正文。要更多可直接引用的内容，优先往 `sources` 里加源站的 RSS。

### 推送渠道（可同时开多个）

```yaml
deliver:
  - type: local_md      # 本地 markdown，永远可用，建议保留
    dir: ./output

  - type: feishu         # 飞书：写云文档最开头 + 可选推聊天
    doc_token: "<文档 URL 最后一段>"
    chat_id: "oc_xxxxxxxx"

  - type: telegram
    chat_id: "@your_channel"

  - type: email
    smtp_host: smtp.qq.com
    to: [you@example.com]
```

**飞书**需要在[开发者后台](https://open.feishu.cn/)建一个自建应用，开通
`docx:document`（云文档读写）和 `im:message`（发消息）两个权限，
把 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 填进 `.env`，
并把应用加进目标文档的协作者（或把文档放到应用可访问的知识库）。

**Telegram**：找 [@BotFather](https://t.me/BotFather) 要 token，
chat_id 可以用 [@userinfobot](https://t.me/userinfobot) 查。

### 配完先自检，别等定时任务

配置对不对，不用等第二天早上收没收到才知道：

```bash
python scripts/check_channels.py                     # 测 config.yaml 里所有渠道
python scripts/check_channels.py --channel telegram  # 只测某一个

python scripts/check_channels.py --create-test-doc   # 飞书：自动建临时文档测完删掉
```

`--create-test-doc` 会新建一个临时文档跑通「写入 + 回读自检」，
再把它删掉，**不会碰到你的正式文档**。（删除需要应用有 `drive:drive` 或
`space:document:delete` 权限；没有的话脚本会提示你手动删那个临时文档。）

---

## 定时运行

### Linux / macOS（crontab）

```bash
./scripts/install_cron.sh          # 默认每天 08:00，可传时间参数
./scripts/install_cron.sh "0 23 * * *"   # 改成每天 23:00
```

### Windows（计划任务）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows_task.ps1 -Time 08:00
```

### GitHub Actions（不用自己的机器）

复制 [`examples/github-actions-daily.yml`](examples/github-actions-daily.yml)
到仓库的 `.github/workflows/daily.yml`，然后在仓库 Settings → Secrets 里
加上 `DEEPSEEK_API_KEY`（以及飞书/Telegram 的密钥）即可，每天自动跑。

---

## 命令行参数

```
python run.py [选项]

  --config PATH        配置文件（默认 ./config.yaml）
  --env PATH           .env 文件路径
  --date YYYY-MM-DD    输出日期（默认今天）
  --window-hours N     采集时间窗小时数（默认 24；周一自动放宽到 72）
  --max-items N        日报最多几条
  --no-llm             不调用大模型，直接排版候选条目
  --no-verify          跳过链接校验（更快，但可能写出死链）
  --dry-run            只生成本地文件，不推送网络渠道
  --out-dir DIR        本地输出目录
  --list-sources       打印配置里的源和渠道
  --self-test          离线自检（不联网），改完代码先跑这个
  --quiet              安静模式
```

退出码：`0` 成功 / `2` 配置错误 / `3` 采集异常 / `4` 无有效条目 /
`5` 模型调用失败 / `6` 有推送渠道失败 —— 方便接监控告警。

---

## 常见问题

**Q：有些源（DigiTimes、EETimes）的条目全被丢了？**
这两个站点的文章页对脚本请求经常返回超时或反爬拦截，条目会被第 2 步自动丢弃 ——
这是设计行为，不是 bug（宁可少写，也不写打不开的链接）。
如果你的网络环境能正常打开它们，补充 `verify.timeout` 到 20~25 秒通常能救回一部分。

**Q：某些链接返回 403，会被丢吗？**
默认不会。`verify.accept_403: true` 把 403 当作"可达但反爬"处理，
因为很多正经媒体对脚本请求就是返回 403。改成 `false` 则会一并丢弃。

**Q：能换成别的行业吗？**
可以，这个项目本质上不绑定半导体：改 `sources`（任何 RSS）、
`digest.title`、和 `semidigest/prompt.py` 里的提示词就行。
`prompt.py` 是整条流水线的核心资产，规则都写在注释里了。

**Q：一天成本多少？**
一次运行约 8k 输入 + 1k 输出 token。DeepSeek 价格下**每天不到 0.01 元**。
RSS 抓取和链接校验不花钱。

**Q：会不会把我的 key 提交到 GitHub？**
不会。`.gitignore` 已经忽略 `.env` 和 `config.yaml`。密钥只从环境变量读，
代码里没有任何硬编码。

---

## 目录结构

```
semiconductor-news-digest/
├── run.py                        # 入口：采集→校验→成稿→审计→推送
├── semidigest/
│   ├── config.py                 # 配置加载（自带极简 .env 解析）
│   ├── collector.py              # RSS/Atom 抓取、时间窗、去重
│   ├── verifier.py               # 并发链接校验（HEAD→GET）
│   ├── prompt.py                 # ⭐ 提示词：整条流水线的核心资产
│   ├── summarizer.py             # LLM 调用 + 无 key 降级排版
│   ├── renderer.py               # 后处理 + 链接白名单审计
│   ├── feishu_blocks.py          # markdown → 飞书 block
│   └── deliver/
│       ├── local_md.py           # 本地 markdown
│       ├── feishu.py             # 飞书云文档 + 聊天
│       ├── telegram.py           # Telegram
│       └── email_smtp.py         # 邮件
├── scripts/                      # 定时任务安装脚本
├── hermes-skill/                 # 可选：给 Hermes Agent 用户的一键集成
├── docs/                         # 分享文案（抖音口播 / 社交平台）
├── examples/                     # 真实示例输出
└── tests/test_smoke.py           # 离线自检
```

---

## 二次开发

```bash
python run.py --self-test      # 改完代码先跑离线自检
python -m pytest tests/ -q     # 或跑测试
python -m semidigest.verifier https://a.com https://b.com   # 单独验链接
```

写新的推送渠道：在 `semidigest/deliver/` 加一个文件，实现
`deliver(options: dict, ctx: DeliveryContext) -> DeliverResult`，
然后在 `deliver/base.py` 的 `CHANNELS` 里注册。

---

## License

MIT，随便用。如果它替我省了点时间，Star 一下就行。
