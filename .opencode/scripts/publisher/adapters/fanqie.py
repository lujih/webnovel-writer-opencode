# .opencode/scripts/publisher/adapters/fanqie.py
"""番茄小说平台适配器。

通过 page.evaluate(fetch) 调用番茄作家后台内部 API。所有请求在浏览器
JS 上下文中执行，自动携带 Cookie 和浏览器指纹。

API 逆向自 fanqienovel.com JS bundle, 2026-02。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from publisher.base import BasePlatform, BookMeta, Chapter, UploadResult

logger = logging.getLogger(__name__)

BASE_URL = "https://fanqienovel.com"
_COMMON_PARAMS = "aid=2503&app_name=muye_novel"

_COMMON_FORM = {"aid": "2503", "app_name": "muye_novel"}

# 女频 genres (gender=0)，其余为男频 (gender=1)
_FEMALE_GENRES = {"言情", "女频", "现代言情", "古代言情", "仙侠言情", "豪门", "穿越", "宫斗"}


def _text_to_html(text: str) -> str:
    """将纯文本转为 HTML 段落。空行分隔段落，段内换行合并。"""
    paragraphs = [p.strip().replace('\n', '') for p in text.split('\n\n') if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paragraphs)


def _clean_protagonist_name(name: str) -> str:
    """去除主角名中的注释和斜杠别名，截断到 20 字。"""
    name = re.sub(r"[（(][^)）]*[)）]", "", name)
    name = name.split("/")[0].strip()
    return name[:20]


async def _page_fetch(
    page,
    method: str,
    path: str,
    form: Optional[dict] = None,
    params: Optional[dict] = None,
) -> object:
    """通过 page.evaluate 在浏览器内执行 fetch，返回 JSON 的 data 字段。"""
    url = f"{BASE_URL}{path}?{_COMMON_PARAMS}"
    if params:
        for k, v in params.items():
            url += f"&{k}={v}"

    form_json = json.dumps(form, ensure_ascii=False) if form else ""

    result = await page.evaluate(
        """async ([url, method, formJson]) => {
            try {
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 30_000);
                const opts = { method, credentials: 'include', signal: controller.signal };
                if (formJson) {
                    const obj = JSON.parse(formJson);
                    opts.body = new URLSearchParams(obj).toString();
                    opts.headers = {
                        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
                    };
                }
                const resp = await fetch(url, opts);
                clearTimeout(timeout);
                const text = await resp.text();
                return { ok: true, status: resp.status, body: text };
            } catch (e) {
                return { ok: false, error: String(e) };
            }
        }""",
        [url, method, form_json],
    )

    if not result.get("ok"):
        raise RuntimeError(f"fetch error: {result.get('error')}")

    raw = result.get("body", "")
    if not raw:
        status = result.get("status", "?")
        raise RuntimeError(f"API {path} returned empty response (status={status})")

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API {path} returned non-JSON: {raw[:200]}") from exc

    if body.get("code") != 0:
        raise RuntimeError(
            f"API {path} failed: {body.get('message') or body.get('msg') or 'unknown'}"
        )

    data = body.get("data")
    return data if data is not None else {}


def _find_category_id(categories: list[dict], genre: str) -> int:
    """在分类列表中匹配最佳 category_id。"""
    for cat in categories:
        name = cat.get("name") or cat.get("category_name", "")
        if name == genre:
            return int(cat["category_id"])
    for cat in categories:
        name = cat.get("name") or cat.get("category_name", "")
        if genre in name or name in genre:
            return int(cat["category_id"])
    if categories:
        logger.warning("No category match for '%s', fallback to %s", genre, categories[0])
        return int(categories[0]["category_id"])
    return 0


def _find_label_ids(labels: list[dict], genre: str, max_count: int = 4) -> list[str]:
    """在标签列表中匹配 genre 相关的 label_id。"""
    def get_name(lb: dict) -> str:
        return lb.get("label_name") or lb.get("name", "")
    def get_id(lb: dict) -> str:
        v = lb.get("label_id") or lb.get("id") or lb.get("category_id")
        return str(v) if v else ""

    selected: list[str] = []
    genre_lower = genre.strip().lower()
    for label in labels:
        name = get_name(label)
        lid = get_id(label)
        if not name or not lid:
            continue
        name_lower = name.lower()
        if genre_lower in name_lower or name_lower in genre_lower:
            selected.append(lid)
        if len(selected) >= max_count:
            break

    if not selected and labels:
        selected = [get_id(l) for l in labels[:2] if get_id(l)]
    return selected


from publisher.adapters import register


@register("fanqie")
class FanqieAdapter(BasePlatform):
    name = "fanqie"
    display_name = "番茄小说"
    login_url = "https://fanqienovel.com/main/writer/?enter_from=author_zone"

    def __init__(self):
        self._mode = "draft"

    def set_mode(self, mode: str):
        self._mode = mode

    # ── 认证 ─────────────────────────────────────────────

    async def setup_auth(self, page) -> bool:
        """打开作家后台，等待用户登录完成。"""
        await page.goto(self.login_url, wait_until="commit", timeout=60_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        if self._is_writer_url(page.url):
            logger.info("Already logged in: %s", page.url)
            return True

        logger.info("Login required, current URL: %s", page.url)
        timeout_ms = 180_000
        elapsed = 0
        while elapsed < timeout_ms:
            await asyncio.sleep(2)
            elapsed += 2000
            if self._is_writer_url(page.url):
                logger.info("Login successful: %s", page.url)
                await asyncio.sleep(3)
                return True

        logger.error("Login timed out")
        return False

    @staticmethod
    def _is_writer_url(url: str) -> bool:
        url_lower = url.lower()
        if any(kw in url_lower for kw in ["login", "passport", "sso", "sign"]):
            return False
        return "fanqienovel.com" in url_lower and any(
            kw in url_lower for kw in ["writer", "main", "author"]
        )

    # ── 页面上下文 ──────────────────────────────────────

    async def _ensure_writer_context(self, page):
        """确保页面在 fanqienovel.com 域名下。空白页上的 fetch 会因
        origin/referer 限制失败，必须先导航到作家后台域名。"""
        if page.url == "about:blank" or "fanqienovel.com" not in page.url:
            await page.goto(self.login_url, wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(3)

    # ── 书单 ─────────────────────────────────────────────

    async def list_books(self, page) -> list[dict]:
        await self._ensure_writer_context(page)
        data = await _page_fetch(
            page, "GET",
            "/api/author/homepage/book_list/v0/",
            params={"page_count": "50", "page_index": "0"},
        )
        if isinstance(data, dict):
            books = data.get("book_list", [])
            return books if isinstance(books, list) else []
        if isinstance(data, list):
            return data
        return []

    # ── 创建书籍 ─────────────────────────────────────────

    async def create_book(self, page, meta: BookMeta) -> str:
        await self._ensure_writer_context(page)
        # 判断 gender
        gender = 0 if any(
            g in meta.genre for g in _FEMALE_GENRES
        ) and not any(
            m in meta.genre for m in ["仙侠", "玄幻", "武侠", "男频", "都市", "科幻"]
        ) else 1

        # 获取分类和标签
        categories = await self._get_category_list(page, gender)
        category_id = _find_category_id(categories, meta.genre)
        labels = await self._get_label_list(page, gender)
        label_ids = _find_label_ids(labels, meta.genre)

        # abstract: 单行, 50+ chars
        abstract = " ".join(
            line.strip() for line in meta.synopsis.splitlines() if line.strip()
        )
        if len(abstract) < 50:
            abstract = abstract + "。" * (50 - len(abstract))

        p1 = _clean_protagonist_name(meta.protagonist)[:5]

        data = await _page_fetch(page, "POST", "/api/author/book/create/v0/", form={
            **_COMMON_FORM,
            "book_name": meta.title,
            "gender": str(gender),
            "abstract": abstract,
            "category_id": str(category_id),
            "original_type": "1",
            "label_id_list": ",".join(label_ids),
            "protagonist_name_1": p1,
            "protagonist_name_2": "",
        })

        book_id = str(data.get("book_id", "")) if isinstance(data, dict) else ""
        if not book_id:
            raise RuntimeError("create_book: no book_id in response")
        logger.info("Book created: id=%s, title=%s", book_id, meta.title)
        return book_id

    async def _get_category_list(self, page, gender: int) -> list[dict]:
        data = await _page_fetch(
            page, "GET",
            "/api/author/book/category_list/v0/",
            params={"gender": gender},
        )
        if isinstance(data, list):
            return data
        return data.get("category_list", []) if isinstance(data, dict) else []

    async def _get_label_list(self, page, gender: int) -> list[dict]:
        data = await _page_fetch(
            page, "GET",
            "/api/author/book/group_category_list/v0/",
            params={"gender": gender},
        )
        labels: list[dict] = []
        if isinstance(data, list):
            labels = data
        elif isinstance(data, dict):
            raw_groups = data.get("group_list") or data.get("label_list") or []
            for group in raw_groups if isinstance(raw_groups, list) else []:
                if isinstance(group, dict):
                    group_labels = group.get("label_list") or group.get("labels") or []
                    if isinstance(group_labels, list):
                        labels.extend(group_labels)
        return labels

    # ── 卷管理 ─────────────────────────────────────────

    async def _get_first_volume(self, page, book_id: str) -> tuple[str, str]:
        data = await _page_fetch(
            page, "GET",
            "/api/author/volume/volume_list/v1/",
            params={"book_id": book_id},
        )
        volumes: list[dict] = []
        if isinstance(data, list):
            volumes = data
        elif isinstance(data, dict):
            volumes = data.get("volume_list", [])
        if not volumes:
            raise RuntimeError(f"No volumes found for book {book_id}")
        vol = volumes[0]
        return str(vol["volume_id"]), vol.get("volume_name", "第一卷：默认")

    # ── 上传章节 ─────────────────────────────────────────

    async def _post_with_retry(self, page, path, form, max_retries=2):
        """POST with retry on empty response. Refreshes writer context between attempts."""
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                result = await _page_fetch(page, "POST", path, form=form)
                return result
            except RuntimeError as e:
                last_exc = e
                if attempt < max_retries:
                    delay = 2 ** attempt  # 1s, 2s exponential backoff
                    await asyncio.sleep(delay)
                    await self._ensure_writer_context(page)
        raise last_exc

    async def upload_chapter(
        self, page, book_id: str, chapter: Chapter
    ) -> UploadResult:
        """上传单章到番茄。两步：new_article 分配 ID → cover_article 保存内容。"""
        await self._ensure_writer_context(page)

        # 预热：验证登录态（使用 login_url 避免 writer 子域名 DNS 不可解析）
        await page.goto(self.login_url, wait_until="networkidle", timeout=30_000)
        if "fanqienovel.com" not in page.url:
            raise RuntimeError("登录态失效: 未跳转到 writer 域名，请重新 setup-auth")

        volume_id, volume_name = await self._get_first_volume(page, book_id)

        # Fanqie 标题格式: "第 X 章 标题" (5-30 chars)
        # 去除 chapter.title 中已有的"第X章"前缀，避免重复
        clean_title = re.sub(r"^第\s*\d+\s*章\s*", "", chapter.title).strip()
        full_title = f"第 {chapter.index} 章 {clean_title}"
        if len(full_title) > 30:
            full_title = full_title[:30]

        html_content = _text_to_html(chapter.content)

        # Step 1: 分配章节槽位（content 不在此阶段发送，服务端忽略）
        new_article_form = {
            **_COMMON_FORM,
            "book_id": book_id,
            "title": full_title,
            "volume_id": volume_id,
            "volume_name": volume_name,
        }
        create_data = await self._post_with_retry(page,
            "/api/author/article/new_article/v0/", form=new_article_form)

        item_id = str(create_data.get("item_id", "")) if isinstance(create_data, dict) else ""
        if not item_id:
            return UploadResult(
                success=False, chapter_index=chapter.index,
                message="new_article 未返回 item_id",
            )

        # Step 2: 写入内容（独立重试，不回退已分配的 item_id，避免孤立文章）
        cover_form = {
            **_COMMON_FORM,
            "book_id": book_id,
            "item_id": item_id,
            "title": full_title,
            "content": html_content,
            "volume_id": volume_id,
            "volume_name": volume_name,
        }
        await self._post_with_retry(page,
            "/api/author/article/cover_article/v0/", form=cover_form)

        logger.info("Chapter saved: item_id=%s, title=%s", item_id, full_title)

        if getattr(self, '_mode', 'draft') == "publish":
            print("  ⚠️ 注意: 当前仅支持草稿保存，章节未正式发布。发布请前往番茄作者后台手动操作。")

        return UploadResult(
            success=True, chapter_index=chapter.index,
            message=f"草稿已保存: {full_title}",
        )

    # ── 草稿管理 ─────────────────────────────────────────
    # 来源：合并自根目录的 fanqie_publish.py, clean_drafts.py, clear_drafts.py
    # 功能：获取草稿列表、删除草稿、发布草稿、清理重复草稿

    async def get_all_drafts(self, page, book_id: str) -> list[dict]:
        """全量获取草稿箱章节列表（支持分页）。
        
        通过两种方式获取：
        1. 导航到草稿箱页面，拦截 draft_list API 响应（首页）
        2. 用 JS fetch 直接调用 API（后续分页）
        
        返回：草稿列表，每个草稿包含 item_id, title, modify_time 等字段
        """
        await self._ensure_writer_context(page)
        
        # 导航到草稿箱页面并拦截响应
        chap_url = f"https://fanqienovel.com/main/writer/chapter-manage/{book_id}?type=2"
        
        all_drafts = []
        total = 0
        page_size = 15
        captured = []

        async def on_resp(resp):
            if 'draft_list' in resp.url and book_id in resp.url:
                try:
                    body = await resp.json()
                    if body.get('code') == 0:
                        data = body.get('data') or {}
                        captured.append({
                            'total': data.get('total_count', 0),
                            'items': data.get('draft_list') or []
                        })
                except Exception:
                    pass

        page.on('response', on_resp)
        await page.goto(chap_url, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        page.remove_listener('response', on_resp)

        for c in captured:
            all_drafts.extend(c['items'])
            total = c['total']

        logger.info("Drafts page 1: %d items, total=%d", len(all_drafts), total)

        # 后续分页：通过 JS 直接调用 API
        page_index = 1
        while len(all_drafts) < total:
            result = await page.evaluate("""async ([book_id, page_index, page_size]) => {
                const params = new URLSearchParams({
                    aid: '2503', app_name: 'muye_novel',
                    book_id,
                    page_index: String(page_index),
                    page_count: String(page_size)
                });
                const r = await fetch(
                    'https://fanqienovel.com/api/author/chapter/draft_list/v1?' + params.toString(),
                    {credentials: 'include'}
                );
                return await r.json();
            }""", [book_id, page_index, page_size])

            items = (result.get('data') or {}).get('draft_list') or [] if isinstance(result, dict) else []
            if not items:
                break
            all_drafts.extend(items)
            logger.info("Drafts page %d: %d items, cumulative=%d", page_index + 1, len(items), len(all_drafts))
            page_index += 1
            if page_index > 50:  # 安全阈值：最多 50 页
                break
            await asyncio.sleep(0.5)

        return all_drafts

    async def delete_draft(self, page, book_id: str, item_id: str) -> bool:
        """删除单个草稿。
        
        Args:
            page: Playwright page 对象
            book_id: 书籍 ID
            item_id: 章节草稿 ID
            
        Returns:
            是否删除成功
        """
        try:
            await _page_fetch(
                page, "POST",
                "/api/author/delete_article/v1",
                form={**_COMMON_FORM, "book_id": book_id, "item_id": item_id}
            )
            return True
        except RuntimeError as e:
            logger.warning("Failed to delete draft %s: %s", item_id, e)
            return False

    async def publish_draft(self, page, book_id: str, item_id: str, 
                           title: str, content: str) -> bool:
        """发布草稿箱中的章节（将草稿状态改为已发布）。
        
        Args:
            page: Playwright page 对象
            book_id: 书籍 ID
            item_id: 草稿 ID
            title: 章节标题
            content: 章节内容（纯文本，会自动转为 HTML）
            
        Returns:
            是否发布成功
        """
        await self._ensure_writer_context(page)
        
        volume_id, volume_name = await self._get_first_volume(page, book_id)
        html_content = _text_to_html(content)
        
        form = {
            **_COMMON_FORM,
            "book_id": book_id,
            "item_id": item_id,
            "volume_id": volume_id,
            "volume_name": volume_name,
            "title": title,
            "content": html_content,
        }
        
        try:
            await _page_fetch(page, "POST", "/api/author/publish_article/v0/", form=form)
            return True
        except RuntimeError as e:
            logger.warning("Failed to publish draft %s: %s", item_id, e)
            return False
