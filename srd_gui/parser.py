"""Pure HTML parsing for spriters-resource.com pages.

Everything here is deterministic and side-effect free so it can be unit
tested against saved HTML. The selectors mirror the site as of 2026:

Game page
    <div class="section" id="section-NNN"><span class="asset-count">[N]</span> Name</div>
    <div class="icondisplay">
        <a class="iconlink" href="/console/game/asset/ID/">
            <div class="iconcontainer">
                <div class="iconheader" title="Title">Title</div>
                <div class="iconbody"><img src="/media/asset_icons/..."></div>
            </div>
        </a> ...
    </div>

Asset page
    <a id="download" href="/media/assets/..." download="Nice Name.png">
    <div class="zoomdisplay"><img src="/media/assets/..."></div>
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

DEFAULT_BASE_URL = "https://www.spriters-resource.com"
SITE_SUFFIX = " - The Spriters Resource"

_ASSET_HREF = re.compile(r"/(?:asset|sheet)/(\d+)/?(?:[?#].*)?$")
_GAME_PATH = re.compile(r"^/([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)/?$")
_COUNT = re.compile(r"\[(\d+)\]")


class InvalidGameUrl(ValueError):
    pass


@dataclass
class Item:
    id: str
    title: str
    url: str
    thumb: str | None
    section: str
    order: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Section:
    id: str
    name: str
    declared_count: int | None
    items: list[Item] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "declared_count": self.declared_count,
            "items": [i.to_dict() for i in self.items],
        }


@dataclass
class Catalog:
    url: str
    console_slug: str
    game_slug: str
    game_title: str
    console_title: str
    sections: list[Section]
    nsfw_enabled: bool | None = None

    @property
    def items(self) -> list[Item]:
        return [i for s in self.sections for i in s.items]

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "console_slug": self.console_slug,
            "game_slug": self.game_slug,
            "game_title": self.game_title,
            "console_title": self.console_title,
            "nsfw_enabled": self.nsfw_enabled,
            "item_count": len(self.items),
            "sections": [s.to_dict() for s in self.sections],
        }


@dataclass
class AssetFile:
    url: str
    filename: str
    title: str
    kind: str  # "download-link" or "image"


def normalize_game_url(url: str, base_url: str = DEFAULT_BASE_URL) -> tuple[str, str, str]:
    """Validate a game URL and return (canonical_url, console_slug, game_slug).

    Accepts the page address with or without scheme, trailing slash, query
    string, or an ``/asset/ID/`` tail (in which case the parent game is used).
    """
    text = (url or "").strip()
    if not text:
        raise InvalidGameUrl("Please paste the game page address.")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    base = urlparse(base_url)
    host = parsed.netloc.lower()
    allowed = {base.netloc.lower(), "spriters-resource.com", "www.spriters-resource.com"}
    if host not in allowed:
        raise InvalidGameUrl("That is not a spriters-resource.com address.")
    path = re.sub(r"/(?:asset|sheet)/\d+/?$", "/", parsed.path)
    if not path.endswith("/"):
        path += "/"
    m = _GAME_PATH.match(path)
    if not m:
        raise InvalidGameUrl(
            "Use the address of a game page, for example "
            "https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/"
        )
    console_slug, game_slug = m.group(1), m.group(2)
    canonical = f"{base.scheme}://{base.netloc}/{console_slug}/{game_slug}/"
    return canonical, console_slug, game_slug


def _own_text(tag: Tag) -> str:
    return " ".join(t.strip() for t in tag.find_all(string=True, recursive=False) if t.strip())


def _section_name(tag: Tag) -> str:
    name = _own_text(tag)
    if name:
        return name
    text = tag.get_text(" ", strip=True)
    text = text.replace("arrow_drop_down", "").replace("arrow_right", "")
    text = _COUNT.sub("", text)
    return re.sub(r"\s+", " ", text).strip() or "Section"


def _is_div_with_class(tag, cls: str) -> bool:
    return isinstance(tag, Tag) and tag.name == "div" and cls in (tag.get("class") or [])


def _items_from_display(display: Tag, section_name: str, base_url: str, start_order: int) -> list[Item]:
    items: list[Item] = []
    order = start_order
    for a in display.find_all("a", href=True):
        href = a["href"]
        m = _ASSET_HREF.search(href)
        if not m:
            continue
        header = a.select_one(".iconheader")
        title = ""
        if header is not None:
            title = (header.get("title") or header.get_text(" ", strip=True) or "").strip()
        img = a.find("img")
        if not title and img is not None:
            title = (img.get("alt") or "").strip()
        if not title:
            title = f"Asset {m.group(1)}"
        thumb = urljoin(base_url, img["src"]) if img is not None and img.get("src") else None
        items.append(Item(
            id=m.group(1),
            title=title,
            url=urljoin(base_url, href),
            thumb=thumb,
            section=section_name,
            order=order,
        ))
        order += 1
    return items


def parse_game_page(html: str, page_url: str, base_url: str = DEFAULT_BASE_URL) -> Catalog:
    """Parse a game page into sections and items, in page order."""
    canonical, console_slug, game_slug = normalize_game_url(page_url, base_url)
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    game_title = h1.get_text(" ", strip=True) if h1 else game_slug
    console_title = console_slug
    if soup.title is not None:
        t = soup.title.get_text(" ", strip=True)
        if t.endswith(SITE_SUFFIX):
            t = t[: -len(SITE_SUFFIX)]
        if t.startswith(game_title + " - "):
            console_title = t[len(game_title) + 3:].strip() or console_slug

    nsfw = None
    toggle = soup.select_one("#nsfw-toggle")
    if toggle is not None:
        txt = toggle.get_text(" ", strip=True).lower()
        if "warning_off" in txt or "visibility_off" in txt:
            nsfw = False
        elif "warning" in txt or "visibility" in txt:
            nsfw = True

    sections: list[Section] = []
    seen: set[str] = set()
    order = 0
    for display in soup.select("div.icondisplay"):
        header = display.find_previous_sibling()
        while header is not None and not _is_div_with_class(header, "section"):
            if _is_div_with_class(header, "icondisplay"):
                header = None
                break
            header = header.find_previous_sibling()
        if header is not None:
            sec_id = header.get("id") or f"section-{len(sections)}"
            name = _section_name(header)
            count_el = header.select_one(".asset-count")
            declared = None
            if count_el is not None:
                cm = _COUNT.search(count_el.get_text())
                declared = int(cm.group(1)) if cm else None
        else:
            sec_id = f"unsectioned-{len(sections)}"
            name = "Uncategorized"
            declared = None
        items: list[Item] = []
        for item in _items_from_display(display, name, base_url, order):
            if item.id in seen:
                continue
            seen.add(item.id)
            item.order = order
            order += 1
            items.append(item)
        if not items:
            continue
        sections.append(Section(id=sec_id, name=name, declared_count=declared, items=items))

    return Catalog(
        url=canonical,
        console_slug=console_slug,
        game_slug=game_slug,
        game_title=game_title,
        console_title=console_title,
        sections=sections,
        nsfw_enabled=nsfw,
    )


def parse_asset_page(html: str, page_url: str, base_url: str = DEFAULT_BASE_URL) -> AssetFile | None:
    """Find the downloadable file on an asset page.

    Preference order: the site's own Download button (which also carries a
    human readable filename), then any anchor with a download attribute,
    then the main displayed image.
    """
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""

    link = soup.select_one("a#download[href]") or soup.select_one("a[download][href]")
    if link is not None:
        href = link.get("data-file") or link["href"]
        fname = (link.get("download") or link.get("data-download") or "").strip()
        if not fname:
            fname = urlparse(urljoin(base_url, href)).path.rsplit("/", 1)[-1]
        return AssetFile(url=urljoin(base_url, href), filename=fname, title=title, kind="download-link")

    img = (soup.select_one("div.zoomdisplay img[src]")
           or soup.select_one("#sheet-container img[src]")
           or soup.select_one("img[src*='/media/assets/']")
           or soup.select_one("img[src*='/resources/sheets/']"))
    if img is not None:
        url = urljoin(base_url, img["src"])
        path_name = urlparse(url).path.rsplit("/", 1)[-1]
        ext = path_name.rsplit(".", 1)[-1] if "." in path_name else "png"
        fname = (title or img.get("alt") or path_name) + "." + ext
        return AssetFile(url=url, filename=fname, title=title, kind="image")
    return None


def looks_like_challenge(html: str) -> bool:
    """True when the HTML is a bot-protection interstitial, not site content."""
    head = (html or "")[:6000]
    return ("Just a moment" in head and "challenge" in head) or "cf-chl" in head or "challenge-platform" in head
