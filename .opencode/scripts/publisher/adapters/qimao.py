# .opencode/scripts/publisher/adapters/qimao.py
"""七猫小说平台适配器。

七猫同属字节系，API 结构与番茄高度相似。基础地址和
路径从 zuozhe.qimao.com 推演，需要实际浏览器验证。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from publisher.adapters import register
from publisher.adapters.fanqie import _page_fetch, _text_to_html, _COMMON_FORM
from publisher.base import BasePlatform, BookMeta, Chapter, UploadResult

logger = logging.getLogger(__name__)

BASE_URL = "https://zuozhe.qimao.com"
_COMMON_PARAMS = "aid=2503&app_name=muye_novel"

# 七猫分类映射（需要实际浏览器验证）
_CATEGORY_MAP: dict[str, int] = {}

# 女频标签
_FEMALE_GENRES = {"言情", "女频", "现代言情", "古代言情", "仙侠言情", "豪门", "穿越", "宫斗"}


def _clean_protagonist_name(name: str) -> str:
    name = re.sub(r"[（(][^)）]*[)）]", "", name)
    name = name.split("/")[0].strip()
    return name[:20]


@register("qimao")
class QimaoAdapter(BasePlatform):
    name = "qimao"
    display_name = "七猫小说"
    login_url = "https://zuozhe.qimao.com"

    _mode: str = "draft"

    def set_mode(self, mode: str):
        self._mode = mode

    def _check_url(self, page_url: str) -> bool:
        return "qimao.com" in page_url.lower()

    # ── 认证 ─────────────────────────────────────────────

    async def setup_auth(self, page) -> bool:
        await page.goto(self.login_url, wait_until="commit", timeout=60_000)
        print("请在浏览器中完成登录（扫码/手机号）...")
        try:
            await page.wait_for_url("**/front/**", timeout=120_000)
            return True
        except Exception:
            return self._check_url(page.url)

    # ── 页面上下文 ──────────────────────────────────────

    async def _ensure_writer_context(self, page):
        if page.url == "about:blank" or "qimao.com" not in page.url:
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
        gender = 0 if any(g in meta.genre for g in _FEMALE_GENRES) \
            and not any(m in meta.genre for m in ["仙侠", "玄幻", "武侠", "男频", "都市", "科幻"]) else 1

        categories = await self._get_category_list(page, gender)
        category_id = self._find_category_id(categories, meta.genre) if categories else 0
        labels = await self._get_label_list(page, gender)
        label_ids = self._find_label_ids(labels, meta.genre) if labels else []

        abstract = " ".join(line.strip() for line in meta.synopsis.splitlines() if line.strip())
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
        return book_id

    def _find_category_id(self, categories, genre: str) -> int:
        """按题材找平台分类 id；匹配不到返回 0。

        此前匹配失败时**静默回落到第一个分类**：

            genre='科幻'  → categories[0]（玄幻）的 id

        作者写「科幻」，书就被发布到「玄幻」分类下，不报错、不提示。七猫的
        分类决定推荐池与读者群，归错类的后果不是"发不出去"，而是"发出去了
        但没人看"——比直接失败更难发现。

        返回 0 让调用方/平台显式失败，是可见的；静默归错类是不可见的。
        """
        target = str(genre or "").strip()
        if not target:
            return 0

        for cat in categories:
            name = str(cat.get("name") or cat.get("category_name", "") or "").strip()
            if name and name == target:
                return int(cat["category_id"])

        for cat in categories:
            name = str(cat.get("name") or cat.get("category_name", "") or "").strip()
            if name and (target in name or name in target):
                return int(cat["category_id"])

        # 匹配不到：返回 0，让失败可见
        return 0

    def _find_label_ids(self, labels, genre: str, max_count: int = 4) -> list[str]:
        def get_name(lb): return str(lb.get("name") or lb.get("label_name", ""))
        def get_id(lb): return str(lb.get("label_id") or lb.get("id", ""))
        selected = []
        genre_lower = genre.strip().lower()
        for label in labels:
            n, lid = get_name(label), get_id(label)
            if not n or not lid:
                continue
            if genre_lower in n.lower() or n.lower() in genre_lower:
                selected.append(lid)
            if len(selected) >= max_count:
                break
        if not selected and labels:
            selected = [get_id(l) for l in labels[:2] if get_id(l)]
        return selected

    async def _get_category_list(self, page, gender: int) -> list[dict]:
        data = await _page_fetch(page, "GET",
            "/api/author/book/category_list/v0/", params={"gender": gender})
        if isinstance(data, list):
            return data
        return data.get("category_list", []) if isinstance(data, dict) else []

    async def _get_label_list(self, page, gender: int) -> list[dict]:
        data = await _page_fetch(page, "GET",
            "/api/author/book/group_category_list/v0/", params={"gender": gender})
        labels = []
        if isinstance(data, list):
            labels = data
        elif isinstance(data, dict):
            raw = data.get("group_list") or data.get("label_list") or []
            for group in raw if isinstance(raw, list) else []:
                if isinstance(group, dict):
                    gl = group.get("label_list") or group.get("labels") or []
                    if isinstance(gl, list):
                        labels.extend(gl)
        return labels

    async def _get_first_volume(self, page, book_id: str):
        data = await _page_fetch(page, "GET",
            "/api/author/book/volume_list/v0/", params={"book_id": book_id})
        volumes = []
        if isinstance(data, list):
            volumes = data
        elif isinstance(data, dict):
            volumes = data.get("volume_list", [])
        if not volumes:
            raise RuntimeError(f"No volumes found for book {book_id}")
        vol = volumes[0]
        return str(vol.get("volume_id", vol.get("id", ""))), vol.get("volume_name", "第一卷")

    # ── 上传章节 ─────────────────────────────────────────

    async def _post_with_retry(self, page, path, form, max_retries=2):
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                return await _page_fetch(page, "POST", path, form=form)
            except RuntimeError as e:
                last_exc = e
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    await self._ensure_writer_context(page)
        raise last_exc

    async def upload_chapter(self, page, book_id: str, chapter: Chapter) -> "UploadResult":
        from publisher.base import UploadResult

        await self._ensure_writer_context(page)
        await page.goto(self.login_url, wait_until="networkidle", timeout=30_000)
        if "qimao.com" not in page.url:
            raise RuntimeError("登录态失效: 未跳转到 qimao 域名，请重新 setup-auth")

        volume_id, volume_name = await self._get_first_volume(page, book_id)

        clean_title = re.sub(r"^第\s*\d+\s*章\s*", "", chapter.title).strip()
        full_title = f"第 {chapter.index} 章 {clean_title}"[:30]

        html_content = _text_to_html(chapter.content)

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
            return UploadResult(success=False, chapter_index=chapter.index,
                                message="new_article 未返回 item_id")

        cover_form = {
            **_COMMON_FORM,
            "book_id": book_id, "item_id": item_id,
            "title": full_title, "content": html_content,
            "volume_id": volume_id, "volume_name": volume_name,
        }
        await self._post_with_retry(page,
            "/api/author/article/cover_article/v0/", form=cover_form)

        if self._mode == "publish":
            print("  ⚠️ 注意: 当前仅支持草稿保存，章节未正式发布。")
        return UploadResult(success=True, chapter_index=chapter.index, message="草稿已保存")
