"""邮件渠道：SMTP/SSL 发送 HTML 日报，可附 markdown 原文。"""

from __future__ import annotations

import html
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Dict, List

from .base import DeliverResult, DeliveryContext


def markdown_to_html(markdown: str) -> str:
    """极简 markdown → HTML：日报用到的语法就这些，不需要引入 markdown 库。"""
    out: List[str] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "---":
            out.append("<hr>")
            continue
        text = html.escape(stripped)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        url_match = re.search(r"https?://\S+", stripped)
        if url_match:
            url = html.escape(url_match.group(0))
            text = re.sub(r"https?://\S+", f'<a href="{url}">{url}</a>', text)
        if stripped.startswith("### "):
            out.append(f"<h3>{text[4:]}</h3>")
        elif stripped.startswith("## "):
            out.append(f"<h2>{text[3:]}</h2>")
        elif stripped.startswith("# "):
            out.append(f"<h1>{text[2:]}</h1>")
        elif re.fullmatch(r"\*\*.+\*\*", stripped):
            out.append(f"<h1>{text}</h1>")
        elif stripped.startswith(("- ", "* ")):
            out.append(f"<p>{text[2:]}</p>")
        else:
            out.append(f"<p>{text}</p>")
    body = "\n".join(out)
    return (
        "<html><body style=\"font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;"
        "line-height:1.7;max-width:760px;margin:0 auto;padding:16px;color:#222\">"
        f"{body}</body></html>"
    )


def deliver(options: Dict[str, Any], ctx: DeliveryContext) -> DeliverResult:
    host = str(options.get("smtp_host") or "").strip()
    port = int(options.get("smtp_port", 465))
    user = ctx.env(options.get("user_env", "SMTP_USER"), True)
    password = ctx.env(options.get("pass_env", "SMTP_PASS"), True)
    recipients = options.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]
    recipients = [str(r).strip() for r in recipients if str(r).strip()]
    if not host:
        return DeliverResult("email", False, "缺少 smtp_host")
    if not recipients:
        return DeliverResult("email", False, "缺少收件人 to")

    message = EmailMessage()
    subject_tpl = options.get("subject", "{date} {title}")
    message["Subject"] = subject_tpl.format(date=ctx.date_str, title=ctx.digest_title)
    message["From"] = options.get("from") or user
    message["To"] = ", ".join(recipients)
    message.set_content(ctx.plain_text)
    message.add_alternative(markdown_to_html(ctx.markdown), subtype="html")

    if options.get("attach_markdown", False):
        message.add_attachment(
            ctx.markdown.encode("utf-8"),
            maintype="text",
            subtype="markdown",
            filename=f"{ctx.date_str}.md",
        )

    use_ssl = bool(options.get("ssl", port == 465))
    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=60, context=context) as server:
            server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(message)

    if ctx.verbose:
        print(f"  [Email] 已发送至 {', '.join(recipients)}")
    return DeliverResult("email", True, f"已发送至 {', '.join(recipients)}", recipients)
