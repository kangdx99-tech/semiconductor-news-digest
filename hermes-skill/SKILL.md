---
name: semiconductor-news-digest
description: "生成并推送半导体新闻日报：采集 RSS → 链接校验 → 大模型成稿 → 链接审计 → 飞书/Telegram/邮件/本地 md 推送。适用于「每天定时产出行业日报」「把一批 RSS 源汇总成中文摘要」「需要日更行业情报并推到飞书」这类需求。附脚本化流水线，可脱离 Agent 独立运行。"
---

# 半导体新闻日报（semiconductor-news-digest）

一个可以脱离 Agent 独立运行的日报流水线。Hermes 里的用法有两种：

1. **推荐：当普通脚本跑**（省 token、结果稳定）—— 用 cronjob 的 `no_agent=True`，
   或者直接用系统 crontab 调用；
2. **当知识库用** —— 需要临时改角度/改源时，让 Agent 读这个 skill 然后手动跑。

## 何时用这个 skill

- 用户说「把今天/昨天的半导体新闻整理一下」「日报推了吗」「加个新闻源」
- 用户抱怨日报漏了/链接打不开/公众号乱码
- 要给**别的行业**做同款日报（改源 + 改提示词即可，见「换行业」）

## 项目位置

`<项目根>/semiconductor-news-digest/`（若迁移过，用 `search_files` 找 `run.py` 里的
`semidigest` 包；包导入靠 `sys.path` 自举，任意目录都能跑）。

## 一键运行

```bash
cd <项目根>/semiconductor-news-digest
python3 run.py --dry-run            # 只生成 output/YYYY-MM-DD.md，不推送
python3 run.py                      # 按 config.yaml 正式跑 + 推送
python3 run.py --self-test          # 离线自检，改完代码先跑这个
python3 run.py --list-sources       # 看当前配了哪些源和渠道
python3 run.py --no-llm --dry-run   # 不调模型，直接排版候选条目
```

退出码：`0` 成功 / `2` 配置错 / `3` 采集异常 / `4` 无有效条目 /
`5` 模型调用失败 / `6` 有推送渠道失败。

## 在 Hermes 里定时跑（推荐姿势）

用 `cronjob` 工具建一个**纯脚本任务**，不要让它走 Agent 循环：

```
cronjob(action='create',
        name='半导体新闻日报',
        schedule='0 23 * * *',
        no_agent=True,
        script='<项目根>/semiconductor-news-digest/run.py',
        deliver='local')
```

- `no_agent=True`：不烧 token，不用模型（模型调用在脚本内部完成），
  输出直接投递。脚本 stdout 为空时静默，所以正常跑完不会打扰用户。
- 判空逻辑由 `run.py` 自己处理：无有效条目会以退出码 4 结束，不会写空日报。
- 副作用（写文档、推聊天）由项目自己的 `deliver` 配置完成，
  **不要**再让 Hermes 的 `send_message` 重复推一遍。

## 为什么这套比「让 Agent 每天现写一遍」好

| | Agent 每天现跑 | 本项目 |
|---|---|---|
| 每次 token | 8k~20k，还要多轮工具调用 | 1 次调用，8k in / 1k out |
| 耗时 | 10~30 分钟（还会中途超时） | 1~2 分钟 |
| 链接 | 靠模型自觉，经常编 | 校验 + 白名单审计，编造链接整块丢弃 |
| 失败模式 | 超预算/超时/工具被安全门拦 | 明确的退出码 |

Hermes 里跑日报曾经踩过的坑（cron 里 `read_file` 读不了 `.env`、
shell 单行拼凭证被安全门拦成 `pending_approval`、工具调用预算耗尽、3 分钟会话超时）
在这套架构里全部不存在 —— 因为整个流程就是一个 Python 进程。

## 关键文件

| 文件 | 作用 |
|---|---|
| `run.py` | 入口，五步流水线 |
| `semidigest/prompt.py` | ⭐ 提示词，日报质量的真正来源，改风格改这里 |
| `semidigest/collector.py` | RSS/Atom 解析、时间窗（周一自动 72h）、去重、关键词预过滤 |
| `semidigest/verifier.py` | 链接校验：HEAD→GET，403 算可达，网络错重试 1 次 |
| `semidigest/renderer.py` | 链接白名单审计：非白名单 URL 整块丢弃 |
| `semidigest/feishu_blocks.py` | markdown → 飞书 block，index=0 只增不删 |
| `config.yaml` | 源、模型、推送渠道（从 `config.example.yaml` 复制） |
| `.env` | 密钥（`DEEPSEEK_API_KEY` / `FEISHU_APP_ID` / `FEISHU_APP_SECRET` …） |

## 混合类源必须标 filter: true

综合科技媒体（IT之家、cnBeta、The Register、Tom's Hardware…）里半导体内容只占一小部分。
不标 `filter: true` 的话，模型一定会写出"苹果市值破 5 万亿""Grok Voice 发布"
这类跟芯片无关的条目 —— 这是实测踩过的坑，详见 `collect.keyword_filter`。

排查这条时先看 `[源] xxx 17 条（关键词过滤掉 43 条）` 这类日志：
过滤比例异常高说明关键词表太窄，异常低说明该给这个源加 `filter: true`。

## 渠道自检（比等定时任务快）

```bash
python3 scripts/check_channels.py --create-test-doc   # 飞书：建临时文档测完自动删
python3 scripts/check_channels.py --channel telegram
```

飞书删除临时文档需要应用有 `drive:drive` 或 `space:document:delete` 权限，
没有的话脚本会打印手动删除链接。

## 排查顺序

1. **没输出** → `python3 run.py --dry-run` 看第几步断的；`[采集]` 行看时间窗后剩几条。
2. **日报是空的/只有 1-2 条** → 多半是网络问题，源全挂了。`python3 run.py --list-sources`
   逐个看 `[源] xxx 0 条`；也可能是时间窗太窄（周末/周一要放宽）。
3. **条目被大量丢弃** → 看 `[丢弃]` 行。`digitimes.com` / `eetimes.com` 的文章页
   对脚本请求会超时，属正常丢弃；想救回来把 `verify.timeout` 提到 20~25 秒，
   或调大 `verify.retries`。
4. **模型编链接** → 审计会兜住（丢块并在 `output/run-<date>.json` 记账）。
   如果频繁出现，说明候选列表里没有好的可引用源，往 `sources` 加源。
5. **飞书写入失败** → 检查应用有没有 `docx:document` 权限、
   有没有把应用加进目标文档协作者；`verify: true` 会在写完回读文档开头自检。
6. **飞书文档被写乱/内容被删** → 几乎不可能：代码路径里没有任何 delete 接口，
   只有 `insert(index=0)`。若文档确实异常，先确认是不是别的任务写错 token 了。

## 换行业

1. `config.yaml` → `sources` 换成目标行业的 RSS；
2. `digest.title` 改名（如「动力电池日报」）；
3. `semidigest/prompt.py` 里改这几处：`SYSTEM_PROMPT` 的身份描述、
   `SECTION_HINTS` 的分组、`USER_TEMPLATE` 第 4 条的正负面清单；
4. 跑 `python3 run.py --self-test` + `--dry-run` 看效果。

## 注意

- `.env` 和 `config.yaml` 已在 `.gitignore` 里；分享给别人的时候别把这两个文件打包进去。
- 项目里的 `deliver` 和 Hermes 的 `send_message` 是两个独立通道，
  同一条日报不要两边都推，不然用户会收到两遍。
- 飞书自建应用建文档已实测可行，但**测试时不要写用户正在用的日报文档** ——
  污染真实文档比不测试更糟。新建一个临时文档测完删掉。
