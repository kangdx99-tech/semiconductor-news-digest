#!/usr/bin/env python3
"""推送渠道连通性自检。

用一小段测试内容真实打一遍渠道，确认权限、token、chat_id / doc_token 都通。
比"等定时任务跑一遍看看有没有收到"快得多。

    python3 scripts/check_channels.py                      # 检查 config.yaml 里配的所有渠道
    python3 scripts/check_channels.py --config config.example.yaml --env ~/.hermes/.env
    python3 scripts/check_channels.py --create-test-doc    # 飞书：自动建临时文档测完删掉
    python3 scripts/check_channels.py --channel telegram

⚠️ 飞书测试请不要指向正在使用的正式文档 —— 脚本也会往里面插内容。
   `--create-test-doc` 会新建一个临时文档，测完自动删除，推荐用它。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from semidigest.config import Config  # noqa: E402
from semidigest.deliver import DeliveryContext, deliver_all  # noqa: E402

SAMPLE_MD = """**渠道自检 · semiconductor-news-digest**

【说明】
这是一条来自 `scripts/check_channels.py` 的连通性测试内容，
确认渠道可用后可以直接忽略并删除。

【测试条目】
① 链接格式与超链接渲染是否正常
来源：自检 | 时间：{date}
这一段用来确认正文、加粗 **bold** 与链接行的渲染效果。
🔗 示例链接 → https://example.com/health-check
"""


def create_temp_doc(cfg: Config) -> str:
    """新建一个用于测试的飞书文档，返回 doc_token。"""
    import requests

    from semidigest.feishu_blocks import API_BASE, tenant_access_token

    token = tenant_access_token(cfg.env("FEISHU_APP_ID"), cfg.env("FEISHU_APP_SECRET"))
    resp = requests.post(
        f"{API_BASE}/docx/v1/documents",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"title": "semidigest 渠道自检（可删）"},
        timeout=30,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"创建临时文档失败：{data}")
    return data["data"]["document"]["document_id"]


def delete_doc(cfg: Config, doc_token: str) -> str:
    """删除临时文档。返回人类可读的结果。"""
    import requests

    from semidigest.feishu_blocks import API_BASE, tenant_access_token

    token = tenant_access_token(cfg.env("FEISHU_APP_ID"), cfg.env("FEISHU_APP_SECRET"))
    resp = requests.delete(
        f"{API_BASE}/drive/v1/files/{doc_token}",
        params={"type": "docx"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    data = resp.json()
    if data.get("code") == 0:
        return f"已删除 https://feishu.cn/docx/{doc_token}"
    if data.get("code") == 99991672:
        return (
            "⚠️ 删除失败：应用缺少 drive:drive 或 space:document:delete 权限。\n"
            f"   请手动删除临时文档：https://feishu.cn/docx/{doc_token}\n"
            "   或在开发者后台补上权限后重跑本脚本的删除逻辑。"
        )
    return f"⚠️ 删除失败（code={data.get('code')}）：{data.get('msg')}\n" \
           f"   请手动删除：https://feishu.cn/docx/{doc_token}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="推送渠道连通性自检")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--channel", help="只测某个渠道（local_md / feishu / telegram / email）")
    parser.add_argument("--create-test-doc", action="store_true",
                        help="飞书：新建临时文档测试，结束后删除")
    parser.add_argument("--keep-test-doc", action="store_true", help="保留临时文档不删除")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)

    cfg = Config.load(args.config, args.env)
    deliveries = cfg.deliveries()
    if args.channel:
        deliveries = [d for d in deliveries if d.get("type") == args.channel]
    if not deliveries:
        print("没有匹配的渠道可测。先看 python3 run.py --list-sources")
        return 2

    temp_doc = ""
    feishu_entries = [d for d in deliveries if d.get("type") in ("feishu", "feishu_doc")]
    if args.create_test_doc and feishu_entries:
        temp_doc = create_temp_doc(cfg)
        print(f"已创建临时文档：https://feishu.cn/docx/{temp_doc}")
        for entry in feishu_entries:
            entry["doc_token"] = temp_doc
            entry["write_doc"] = True
            entry["push_chat"] = False        # 自检不往聊天里灌测试内容
            entry["verify"] = True
            entry["verify_marker"] = args.date
        # 只测文档写入时不重复测聊天
        deliveries = [d for d in deliveries if d.get("type") not in ("feishu", "feishu_doc")] + feishu_entries

    ctx = DeliveryContext(
        markdown=SAMPLE_MD.format(date=args.date),
        date_str=args.date,
        digest_title="渠道自检",
        base_dir=cfg.base_dir,
        env=cfg.env,
        dry_run=False,
        verbose=True,
        extra={"timestamp": datetime.now().strftime("%Y-%m-%d_%H%M%S")},
    )

    results = deliver_all(deliveries, ctx)
    print("\n自检结果：")
    for result in results:
        print(result.line())

    if temp_doc and not args.keep_test_doc:
        print(f"\n删除临时文档：{delete_doc(cfg, temp_doc)}")
    elif temp_doc:
        print(f"\n临时文档保留：https://feishu.cn/docx/{temp_doc}（记得自己删）")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
