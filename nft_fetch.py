"""Telegram NFT (sovg'a) havolasidan ma'lumot olish: https://t.me/nft/PlushPepe-123
Olinadi: nom, Model / Orqa fon / Belgi (+ foizlari) va animatsiya (.tgs) yoki rasm (.webp)."""
import html as _html
import logging
import re

log = logging.getLogger(__name__)

LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?t\.me/nft/([A-Za-z0-9_]+)-(\d+)", re.I)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NftBot/1.0)", "Accept-Language": "en"}
_LABELS = {"model": "model", "backdrop": "backdrop", "symbol": "symbol"}


def parse_link(text: str):
    """'t.me/nft/PlushPepe-123' -> ('PlushPepe', '123') yoki None."""
    m = LINK_RE.search(text or "")      # matn ichida qayerda bo'lsa ham topadi: "[ t.me/nft/MythMask-85237 ]" ham bo'ladi
    return (m.group(1), m.group(2)) if m else None


def pretty_title(name: str, num: str) -> str:
    """'PlushPepe', '123' -> 'Plush Pepe #123'"""
    return f"{re.sub(r'(?<!^)(?=[A-Z])', ' ', name)} #{num}"


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]*>", " ", s)
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def _split_pct(cell: str):
    """'Gold Star 1.5%' -> ('Gold Star', '1.5%')"""
    cell = _clean(cell)
    m = re.search(r"([\d]+(?:[.,]\d+)?)\s*%", cell)
    if not m:
        return cell[:100] or None, None
    name = (cell[:m.start()] + " " + cell[m.end():]).strip()
    return (name[:100] or None), f"{m.group(1).replace(',', '.')}%"


def parse_page(page: str) -> dict:
    """t.me/nft sahifasi HTML'idan model/backdrop/symbol ni ajratadi. Topilmasa kalitlar bo'lmaydi."""
    out: dict = {}
    # 1) jadval qatorlari: <tr><th>Model</th><td>Gold Star <mark>1.5%</mark></td></tr>
    for row in re.findall(r"<tr[^>]*>.*?</tr>", page, flags=re.S | re.I):
        th = re.search(r"<th[^>]*>(.*?)</th>", row, flags=re.S | re.I)
        td = re.search(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)
        if not th or not td:
            continue
        key = _LABELS.get(_clean(th.group(1)).lower())
        if key and key not in out:
            name, pct = _split_pct(td.group(1))
            if name:
                out[key], out[key + "_pct"] = name, pct
    # 2) zaxira: meta description "Model: X\nBackdrop: Y\nSymbol: Z"
    if not all(k in out for k in _LABELS.values()):
        for mt in re.findall(r"<meta[^>]+(?:twitter|og):description[^>]*>", page, flags=re.I):
            c = re.search(r'content=["\']([^"\']*)["\']', mt, flags=re.I)
            if not c:
                continue
            desc = _html.unescape(c.group(1))
            for key in _LABELS.values():
                if key in out:
                    continue
                mm = re.search(rf"{key}\s*:\s*(.+?)(?=\s*(?:model|backdrop|symbol|quantity|owner)\s*:|\n|$)",
                               desc, flags=re.I)
                if mm:
                    name, pct = _split_pct(mm.group(1))
                    if name:
                        out[key], out[key + "_pct"] = name, pct
    og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', page, flags=re.I)
    if og:
        out["og_image"] = _html.unescape(og.group(1))
    return out


async def fetch_nft(name: str, num: str) -> dict | None:
    """NFT haqida to'liq ma'lumot. Qaytaradi: title, link, model.., backdrop.., symbol.., tgs (bytes), webp (bytes), photo (bytes).
    NFT topilmasa yoki tarmoq xatosi bo'lsa None."""
    import httpx  # kech import: parse_page ni httpx'siz ham sinash mumkin

    link = f"https://t.me/nft/{name}-{num}"
    slug = f"{name.lower()}-{num}"
    data: dict = {"title": pretty_title(name, num), "link": link}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=_HEADERS) as cl:
        try:
            r = await cl.get(link)
            r.raise_for_status()
            data.update(parse_page(r.text))
        except Exception as e:
            log.warning("NFT sahifasi olinmadi %s: %s", link, e)
            return None
        if not any(k in data for k in ("model", "backdrop", "symbol")):
            return None  # bunday NFT yo'q (Telegram umumiy sahifa qaytargan)

        async def get(url: str, min_len: int = 100):
            try:
                x = await cl.get(url)
                if x.status_code == 200 and len(x.content) >= min_len:
                    return x.content
            except Exception as e:
                log.info("media olinmadi %s: %s", url, e)
            return None

        tgs = await get(f"https://nft.fragment.com/gift/{slug}.tgs")
        data["tgs"] = tgs if tgs and tgs[:2] == b"\x1f\x8b" else None   # .tgs — gzip
        if not data["tgs"]:
            data["webp"] = await get(f"https://nft.fragment.com/gift/{slug}.webp")
        if not data["tgs"] and not data.get("webp") and data.get("og_image"):
            data["photo"] = await get(data["og_image"])
    return data
