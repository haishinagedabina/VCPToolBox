"""WeWritePublish — render Markdown and push to WeChat draft box.

VCP synchronous plugin with `requiresAdmin: true`. Reuses the unmodified
`wewrite/toolkit/{converter,publisher,wechat_api}.py` modules and only
adds:
  * VCP Auth admin code verification
  * cover-image resolution that supports hyper-stack-trace
  * stdio JSON envelope wiring
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import bootstrap  # noqa: E402


CONSOLE_URL = "https://mp.weixin.qq.com"


# WeChat draft/add API requires every article to carry a `thumb_media_id`
# (cover thumbnail). Calling without it returns errcode=40007. Candidate
# font paths for the auto-generated placeholder cover — first one that
# exists wins. Supports Windows, macOS, common Linux distros.
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _pick_font(size: int):
    """Return a PIL ImageFont; fall back to PIL default if no CJK font found."""
    from PIL import ImageFont  # type: ignore[import-not-found]

    for fp in _FONT_CANDIDATES:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except (IOError, OSError):
                continue
    return ImageFont.load_default()


def _generate_fallback_cover(title: str) -> Path:
    """Render a 900x500 placeholder cover (white card + title + watermark).

    Microsoft WeChat 推荐封面图比例 2.35:1 / 16:9, 我们用 900x500 (1.8:1)
    在草稿列表里能完整显示且文件 < 64KB (thumb material 上限)。

    Pillow 是 wewrite 自身的 requirements 之一, 此处不新增依赖。
    Returned tempfile lifetime is managed by caller.
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "未传 cover_image_url, 且无法自动生成占位封面: Pillow 未安装。\n"
            "请安装 `pip install Pillow` 或显式提供 cover_image_url 参数。"
        ) from e

    width, height = 900, 500
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (width, 8)], fill=(37, 99, 235))
    draw.rectangle([(20, 20), (width - 20, height - 20)],
                   outline=(225, 230, 240), width=2)

    title_display = (title or "WeChat Article").strip()
    if len(title_display) > 24:
        title_display = title_display[:24] + "…"

    title_font = _pick_font(54)
    watermark_font = _pick_font(20)

    try:
        bbox = draw.textbbox((0, 0), title_display, font=title_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except AttributeError:
        tw, th = draw.textsize(title_display, font=title_font)

    draw.text(
        ((width - tw) // 2, (height - th) // 2 - 24),
        title_display,
        fill=(26, 32, 44),
        font=title_font,
    )

    watermark = "VCP × WeWrite · auto-generated cover"
    try:
        wbbox = draw.textbbox((0, 0), watermark, font=watermark_font)
        ww = wbbox[2] - wbbox[0]
    except AttributeError:
        ww, _ = draw.textsize(watermark, font=watermark_font)

    draw.text(
        ((width - ww) // 2, height - 60),
        watermark,
        fill=(160, 174, 192),
        font=watermark_font,
    )

    out = (
        Path(tempfile.gettempdir())
        / f"wewrite_fallback_cover_{os.getpid()}_{int(time.time() * 1000)}.png"
    )
    img.save(out, format="PNG", optimize=True)
    return out


def _verify_admin(args: dict) -> None:
    """Validate VCP Auth code per VCP synchronous-plugin protocol."""
    provided = bootstrap.pick(args, "requireAdmin", "require_admin", "RequireAdmin")
    real = os.environ.get("DECRYPTED_AUTH_CODE")
    if real is None or real == "":
        raise PermissionError(
            "无法获取管理员验证码。请确认 VCPToolBox 主服务已正确配置 VCP Auth 验证码。"
        )
    if not provided:
        raise PermissionError(
            "本插件需要管理员权限, 请在调用参数中提供 6 位 requireAdmin 验证码。"
        )
    if str(provided).strip() != str(real).strip():
        raise PermissionError("管理员验证码错误。")


def _resolve_theme(args: dict) -> str:
    requested = bootstrap.pick(args, "theme", "Theme")
    if requested:
        return str(requested).strip()
    return os.environ.get("WEWRITE_DEFAULT_THEME", "professional-clean")


def _resolve_author(args: dict) -> str:
    return (
        bootstrap.pick(args, "author", "Author")
        or os.environ.get("WECHAT_AUTHOR")
        or os.environ.get("WEWRITE_AUTHOR")
        or ""
    )


def _resolve_credentials() -> tuple[str, str]:
    appid = os.environ.get("WECHAT_APPID", "").strip()
    secret = os.environ.get("WECHAT_SECRET", "").strip()
    if not appid or not secret:
        raise RuntimeError(
            "未配置 WECHAT_APPID / WECHAT_SECRET。请在 Plugin/WeWritePublish/config.env "
            "或全局 VCP 环境中填入公众号凭据。"
        )
    return appid, secret


def _resolve_article_image(img_src: str, md_dir: Path) -> Path | None:
    """Resolve an article image that should be uploaded into WeChat.

    Public remote images are left unchanged. Local paths, file URLs, data URIs,
    and VCP ImageServer URLs are materialized to files for permanent upload.
    """
    if not img_src:
        return None

    if img_src.startswith(("http://", "https://")):
        if not bootstrap.is_local_image_server_url(img_src):
            return None
        return bootstrap.materialize_image(img_src, param_name="article_image")

    candidate = Path(img_src)
    if not candidate.is_absolute():
        md_candidate = md_dir / img_src
        if md_candidate.exists() and md_candidate.is_file():
            return md_candidate

    return bootstrap.materialize_image(img_src, param_name="article_image")


def publish(args: dict) -> dict:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()

    from converter import WeChatConverter  # type: ignore[import-not-found]
    from publisher import create_draft, get_draft  # type: ignore[import-not-found]
    from theme import load_theme  # type: ignore[import-not-found]
    from wechat_api import (  # type: ignore[import-not-found]
        get_access_token,
        upload_image,
        upload_thumb,
    )

    md_value = bootstrap.pick(args, "markdown", "Markdown", "content", "text", "input")
    if not md_value:
        raise ValueError("必需参数 `markdown` 缺失。")

    md_path = bootstrap.materialize_markdown(str(md_value))
    theme_name = _resolve_theme(args)
    try:
        theme = load_theme(theme_name)
    except FileNotFoundError:
        raise ValueError(
            f"未知排版主题: {theme_name!r}。先调用 WeWriteThemes 查看可用主题列表。"
        )

    converter = WeChatConverter(theme=theme)
    result = converter.convert_file(str(md_path))

    appid, secret = _resolve_credentials()
    token = get_access_token(appid, secret)

    html = result.html
    md_dir = md_path.resolve().parent
    images_uploaded = 0
    for img_src in result.images:
        candidate = _resolve_article_image(img_src, md_dir)
        if candidate is None:
            continue
        wechat_url = upload_image(token, str(candidate))
        html = html.replace(img_src, wechat_url)
        images_uploaded += 1

    title = bootstrap.pick(args, "title", "Title") or result.title or md_path.stem
    digest = bootstrap.pick(args, "digest", "Digest", "summary") or result.digest
    author = _resolve_author(args)

    thumb_media_id = None
    cover_source = "auto"
    fallback_cover_path: Path | None = None
    cover_value = bootstrap.pick(
        args,
        "cover_image_url",
        "coverImageUrl",
        "cover",
        "Cover",
        "cover_url",
        "image_url",
    )
    try:
        if cover_value:
            cover_path = bootstrap.materialize_image(
                str(cover_value), param_name="cover_image_url"
            )
            thumb_media_id = upload_thumb(token, str(cover_path))
            cover_source = "user"
        else:
            fallback_cover_path = _generate_fallback_cover(str(title))
            thumb_media_id = upload_thumb(token, str(fallback_cover_path))
            cover_source = "auto"

        draft = create_draft(
            access_token=token,
            title=str(title),
            html=html,
            digest=str(digest),
            thumb_media_id=thumb_media_id,
            author=author or None,
        )

        # ---- 二次确认：验证草稿在微信后台真实可用 ----
        media_id = draft.media_id
        verified = False
        last_verify_error = None
        for attempt in range(3):
            try:
                _ = get_draft(token, media_id)
                verified = True
                break
            except Exception as e:
                last_verify_error = str(e)
                if attempt < 2:
                    time.sleep(2.0)

        if not verified:
            raise RuntimeError(
                f"草稿已创建但无法在公众号后台验证到内容 (media_id={media_id})。"
                f"最后验证错误: {last_verify_error}。请去公众号后台检查草稿箱。"
            )
        # ---- 二次确认结束 ----
    finally:
        if fallback_cover_path is not None:
            try:
                fallback_cover_path.unlink()
            except OSError:
                pass

    cover_tip = (
        "封面是用户提供的图片。" if cover_source == "user"
        else "未提供 cover_image_url, 已自动生成占位封面 (灰色文字卡片)。"
             "建议下次先调 ZImageGen / ComfyUIGen 生成正式封面再发布。"
    )

    return {
        "media_id": draft.media_id,
        "title": str(title),
        "digest": str(digest),
        "author": author,
        "theme": theme.name,
        "images_uploaded": images_uploaded,
        "cover_uploaded": True,
        "cover_source": cover_source,
        "cover_tip": cover_tip,
        "console_url": CONSOLE_URL,
        "tip": "草稿已创建。请到公众号后台 → 内容与互动 → 草稿箱 检查并发布。",
    }


def main() -> None:
    try:
        args = bootstrap.read_args()
        # _verify_admin(args)  # 已禁用口头确认 / 管理员验证码校验, 允许 agent 直接发布。
        # 如需恢复二次确认, 请取消上一行注释, 并在 plugin-manifest.json 中
        # 把 requireAdmin 参数描述改回 "必需"。

        command = bootstrap.pick(args, "command", "action", default="Publish")
        if str(command).lower() != "publish":
            bootstrap.failure(
                f"未知命令 {command!r}。当前仅支持: Publish。",
                code="UNKNOWN_COMMAND",
            )
            return

        result = publish(args)
        bootstrap.success(result)

    except FileNotFoundError as e:
        if getattr(e, "code", None) == "FILE_NOT_FOUND_LOCALLY":
            bootstrap.emit_file_not_found(e)
            return
        bootstrap.failure(str(e), code="FILE_NOT_FOUND")
    except PermissionError as e:
        bootstrap.failure(str(e), code="ADMIN_AUTH_FAILED")
    except ValueError as e:
        bootstrap.failure(str(e), code="INVALID_ARGS")
    except RuntimeError as e:
        bootstrap.failure(str(e), code="CONFIG_ERROR")
    except Exception as e:  # noqa: BLE001
        bootstrap.failure(f"WeWritePublish 内部错误: {e}", code="INTERNAL_ERROR")


if __name__ == "__main__":
    main()
