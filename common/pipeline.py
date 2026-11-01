"""Общие функции конвейера translate-tools.

Скрипты 01-extract, 02-apply и 03-generate используют этот модуль, чтобы
не дублировать разбор pipe-таблиц, работу со шрифтами и патч TMP-атласа.
"""
import io
import json
import os
import re
import struct
from collections import Counter

CYR = re.compile("[\u0400-\u04FF]")
CYR_MARKER = "здоров".encode("utf-8")   # признак уже переведённого файла
HEADER_HINTS = ("English", "Item ID", "Poor Name")
GLYPH_RECORD = 52
CHAR_RECORD = 16
ONE_FLOAT = 0x3F800000

# служебные таблицы, которые не переводятся
BLACKLIST = {
    "delusionItemData_Master",
    "delusionPlayerHouse_Upgrades",
    "delusionAlchemyComboData_Master",
    "credits_Eng",
}


# ---------------------------------------------------------------- настройки --
def load_settings(root):
    path = os.path.join(root, "data", "settings.json")
    with io.open(path, encoding="utf-8-sig") as f:
        return json.load(f)


# ---------------------------------------------------------------- версия игры --
def sha256_file(path, chunk=1024 * 1024):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def load_versions(root):
    path = os.path.join(root, "data", "versions.json")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8-sig") as f:
        return json.load(f).get("versions", [])


def detect_version(root, assets_path):
    """Опознаёт версию игры по SHA-256 чистого resources.assets.

    Возвращает словарь: version, known, sha256, size, entry (совпавшая запись).
    """
    sha = sha256_file(assets_path)
    size = os.path.getsize(assets_path)
    for entry in load_versions(root):
        if entry.get("sha256") and entry["sha256"].lower() == sha:
            return {"version": entry.get("version", "?"), "known": True,
                    "sha256": sha, "size": size, "entry": entry}
    return {"version": None, "known": False, "sha256": sha, "size": size, "entry": None}


def save_work_version(work_dir, info):
    path = os.path.join(work_dir, "version.json")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(info, ensure_ascii=False, indent=2))
    return path


def load_work_version(work_dir):
    path = os.path.join(work_dir, "version.json")
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def game_version_hint(game_dir):
    """Подсказка из «Game version.txt», если хеш не опознан."""
    for name in ("Game version.txt", "Game version.txt".lower()):
        path = os.path.join(game_dir, name)
        if os.path.exists(path):
            with io.open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read().strip()
            return " ".join(text.split())
    return None


def has_cyr(text):
    return bool(text) and bool(CYR.search(text))


def cyr_chars(text):
    return {c for c in text if "\u0400" <= c <= "\u04FF"}


# ------------------------------------------------------------------ таблицы --
def parse_table(text):
    """pipe-таблица -> {'newline', 'preamble', 'header', 'rows'}."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    header_idx = None
    for i, line in enumerate(lines[:8]):
        if "|" in line and any(h in line for h in HEADER_HINTS):
            header_idx = i
            break
    if header_idx is None:
        for i, line in enumerate(lines[:8]):
            if "|" in line and line.split("|")[0].strip().startswith("ID"):
                header_idx = i
                break
    if header_idx is None:
        return None
    return {
        "newline": newline,
        "preamble": lines[:header_idx],
        "header": lines[header_idx].split("|"),
        "rows": [l.split("|") for l in lines[header_idx + 1:]],
    }


def table_to_text(table):
    return table["newline"].join(
        table["preamble"] + ["|".join(table["header"])] + ["|".join(c) for c in table["rows"]]
    )


def target_columns(table, column_name="English"):
    """Колонки, в которые пишется перевод.

    Сначала ищем колонки с названием целевого языка (English / English Title /
    English Text ...). Если таких нет (например, в таблицах предметов названия
    колонок другие), берём все колонки кроме первой (ключа).
    """
    name = (column_name or "english").strip().lower()
    cols = [i for i, header in enumerate(table["header"])
            if i > 0 and name in header.strip().lower()]
    if cols:
        return cols
    return list(range(1, len(table["header"])))


def data_rows(table):
    """Непустые строки таблицы (у которых есть ключ)."""
    return [cells for cells in table["rows"] if cells and cells[0].strip()]


def key_width(table, limit=3):
    """Сколько первых колонок образует ключ строки.

    В большинстве таблиц первая колонка уникальна. Но есть таблицы, где она
    повторяется: в `delusionTasks_allLanguages` первая колонка — номер стадии,
    и одинаковые номера встречаются во всех задачах (40 строк с ID «10»).
    Тогда ключом становится пара «вторая колонка|первая» (parent|ID), иначе
    перевод одной задачи разъезжается по всем остальным.
    """
    rows = data_rows(table)
    if not rows:
        return 1
    width = 1
    max_width = min(limit, len(table["header"]))
    while width < max_width:
        keys = [row_key(cells, width) for cells in rows]
        if len(set(keys)) == len(keys):
            return width
        width += 1
    return width


def row_key(cells, width=1, sep="|"):
    """Ключ строки: первая колонка, а при неуникальности — «parent|ID»."""
    parts = [cells[i].strip() for i in range(min(width, len(cells)))]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return sep.join(reversed(parts))


# --------------------------------------------------------------- переводы ----
def load_tsv(path):
    """TSV: `ключ<TAB>перевод` или `ключ<TAB>номер колонки<TAB>перевод`."""
    result = {}
    if not os.path.exists(path):
        return result
    with io.open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            key = parts[0].strip()
            if not key:
                continue
            if len(parts) >= 3 and parts[1].strip().isdigit():
                col, value = int(parts[1]), parts[2].strip()
            else:
                col, value = None, parts[1].strip()
            if value:
                result[(key, col)] = value
    return result


def save_tsv(path, pairs):
    """pairs: {(ключ, колонка|None): перевод} или {ключ: перевод}."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# ключ\t[колонка]\tперевод\n")
        normalized = {}
        for key, value in pairs.items():
            if isinstance(key, tuple):
                normalized[key] = value
            else:
                normalized[(key, None)] = value
        for key, col in sorted(normalized, key=lambda k: (k[0], -1 if k[1] is None else k[1])):
            value = normalized[(key, col)]
            if col is None:
                f.write(f"{key}\t{value}\n")
            else:
                f.write(f"{key}\t{col}\t{value}\n")


def load_translations(root, table):
    path = os.path.join(root, "data", "translations", table + ".tsv")
    return load_tsv(path)


def load_dictionary(root):
    """Словарь «английский текст -> перевод»: ключ — сам текст, без колонки."""
    raw = load_tsv(os.path.join(root, "data", "dictionary.tsv"))
    return {text: value for (text, _col), value in raw.items()}


def apply_translations(table, translations, dictionary, column_name="English"):
    """Проставляет переводы в целевые колонки таблицы.

    Ключ строки берётся составным, если первая колонка не уникальна (см.
    `key_width`), поэтому перевод не может попасть в чужую задачу. Короткий
    ключ из одной первой колонки принимается только тогда, когда такая строка
    в таблице единственная.

    Возвращает (сколько ячеек заменено, сколько ключей перевода не пригодилось).
    """
    cols = target_columns(table, column_name)
    width = key_width(table)
    plain_count = Counter(cells[0].strip() for cells in data_rows(table))
    used_keys = set()
    changed = 0
    for cells in table["rows"]:
        if not cells or not cells[0].strip():
            continue
        full = row_key(cells, width)
        candidates = [full]
        plain = cells[0].strip()
        if width > 1 and plain_count.get(plain, 0) == 1:
            candidates.append(plain)
        for col in cols:
            if len(cells) <= col:
                continue
            ru = None
            hit = None
            for key in candidates:
                ru = translations.get((key, col))
                if ru is None:
                    ru = translations.get((key, None))
                if ru is not None:
                    hit = key
                    break
            if ru is None:
                # точное совпадение английского текста по общему словарю
                ru = dictionary.get(cells[col].strip())
            if ru is None:
                continue
            if cells[col] != ru:
                cells[col] = ru
                changed += 1
            if hit:
                used_keys.add(hit)
    stale = len({k for k, _ in translations} - used_keys)
    return changed, stale


# ------------------------------------------------------------------ шрифты ---
def ttf_cmap(path):
    from fontTools.ttLib import TTFont
    with open(path, "rb") as f:
        head = TTFont(io.BytesIO(f.read()), fontNumber=0, lazy=True)
    return set(head.getBestCmap() or {})


def font_coverage(fonts_dir, needed):
    """Сколько из нужных символов покрывает каждый TTF из data/fonts."""
    report = {}
    for name in sorted(os.listdir(fonts_dir)):
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        try:
            cmap = ttf_cmap(os.path.join(fonts_dir, name))
        except Exception as exc:
            report[name] = (0, len(needed), f"ошибка чтения: {exc}")
            continue
        missing = sorted(c for c in needed if ord(c) not in cmap)
        report[name] = (len(needed) - len(missing), len(needed), "".join(missing))
    return report


def patch_fonts(env, root, fonts_cfg):
    """Подменяет TrueType-данные в Font-ассетах (m_FontData)."""
    patch_targets = {}
    for asset_name, rel in fonts_cfg.items():
        path = os.path.join(root, "data", rel)
        if not os.path.exists(path):
            print(f"  ! нет файла шрифта {path}")
            continue
        with open(path, "rb") as f:
            patch_targets[asset_name] = f.read()

    font_ids, patched = {}, []
    for obj in env.objects:
        if obj.type.name != "Font":
            continue
        tree = obj.read_typetree()
        name = tree.get("m_Name")
        font_ids[name] = obj.path_id
        data = patch_targets.get(name)
        if data is None:
            continue
        tree["m_FontData"] = data
        obj.save_typetree(tree)
        patched.append((name, len(data)))
    return font_ids, patched


# --------------------------------------------------------------------- TMP ---
def find_char_table(raw):
    """Ищет m_CharacterTable: записи (int 1, unicode, индекс глифа, float 1.0)."""
    n = len(raw)
    best = None
    off = 0
    while off + CHAR_RECORD <= n:
        if (struct.unpack_from("<i", raw, off)[0] == 1
                and 0x20 <= struct.unpack_from("<I", raw, off + 4)[0] <= 0x2FFF
                and struct.unpack_from("<i", raw, off + 12)[0] == ONE_FLOAT):
            start, prev, count = off, -1, 0
            while off + CHAR_RECORD <= n:
                if struct.unpack_from("<i", raw, off)[0] != 1:
                    break
                uni = struct.unpack_from("<I", raw, off + 4)[0]
                if not (0x20 <= uni <= 0x2FFF) or uni <= prev:
                    break
                if struct.unpack_from("<i", raw, off + 12)[0] != ONE_FLOAT:
                    break
                prev, count = uni, count + 1
                off += CHAR_RECORD
            if count >= 20 and (best is None or count > best[1]):
                best = (start, count)
            continue
        off += 4
    return best


def make_tmp_dynamic(raw, atlas_size, old_size, font_pid):
    """TMP-ассет -> динамический, под старым атласом появляется свободная полоса."""
    found = find_char_table(raw)
    if not found:
        return None, "таблица символов не найдена"
    cstart, count = found
    if struct.unpack_from("<i", raw, cstart - 4)[0] != count:
        return None, "не сошёлся счётчик символов"

    data = bytearray(raw)
    tail = cstart + count * CHAR_RECORD

    m = re.search(rb"[0-9a-f]{32}", data)
    if not m:
        return None, "не найден GUID исходного шрифта"
    src = (m.start() - 4 + 4 + 32 + 3) & ~3
    struct.pack_into("<iq", data, src, 0, font_pid)   # m_SourceFontFile
    struct.pack_into("<i", data, src + 12, 1)          # Dynamic
    struct.pack_into("<i", data, src + 16, 1)          # мультиатлас

    n_atlas = struct.unpack_from("<i", data, tail)[0]
    used_off = tail + 4 + 12 * n_atlas + 12
    used_count = struct.unpack_from("<i", data, used_off)[0]
    free_off = used_off + 4 + used_count * 16
    free_count = struct.unpack_from("<i", data, free_off)[0]
    if free_count == 0:
        return None, "нет списка свободных прямоугольников"
    struct.pack_into("<iiii", data, free_off + 4, 0, old_size,
                     atlas_size, atlas_size - old_size)

    replaced = 0
    for off in range(0, len(data) - 8, 4):
        if struct.unpack_from("<ii", data, off) == (old_size, old_size):
            struct.pack_into("<ii", data, off, atlas_size, atlas_size)
            replaced += 1
    return bytes(data), (f"символов={count}, свободная полоса "
                         f"({old_size}..{atlas_size}), полей размера={replaced}")


def enlarge_atlas(env, texture_names, size):
    """Атлас TMP: увеличивает текстуру, сохраняя старые пиксели в левом верхнем углу."""
    done = []
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        tree = obj.read_typetree()
        name = tree.get("m_Name")
        if name not in texture_names:
            continue
        width, height = tree.get("m_Width"), tree.get("m_Height")
        if width == size and height == size:
            done.append(name)
            continue
        pixels = tree.get("image data") or b""
        if isinstance(pixels, list):
            pixels = bytes(pixels)
        if len(pixels) < width * height:
            print(f"  ! {name}: мало данных ({len(pixels)}), пропуск")
            continue
        new = bytearray(size * size)
        for y in range(height):
            new[y * size:y * size + width] = pixels[y * width:(y + 1) * width]
        tree["m_Width"] = size
        tree["m_Height"] = size
        tree["m_CompleteImageSize"] = size * size
        tree["m_MipCount"] = 1
        tree["m_MipsStripped"] = 0
        tree["m_IsReadable"] = True
        tree["image data"] = bytes(new)
        obj.save_typetree(tree)
        done.append(name)
    return done


def patch_tmp(env, cfg, font_ids):
    """Переводит TMP-шрифты в динамический режим и добавляет место под кириллицу."""
    atlas_size = cfg.get("atlas_size", 1024)
    old_size = cfg.get("old_atlas_size", 512)
    source_font = cfg["source_font"]
    font_pid = font_ids.get(source_font)
    if font_pid is None:
        return []
    enlarged = enlarge_atlas(env, {cfg["atlas_texture"]}, atlas_size)
    result = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        raw = obj.get_raw_data()
        if len(raw) < 200 or cfg["asset"].encode() not in raw[:200]:
            continue
        data, info = make_tmp_dynamic(raw, atlas_size, old_size, font_pid)
        if data is None:
            result.append((cfg["asset"], f"пропуск: {info}"))
            continue
        obj.set_raw_data(data)
        result.append((cfg["asset"], info))
    return enlarged, result


# --------------------------------------------------------------- сохранение --
def pick_clean_assets(game_dir, rel_assets):
    """Первый файл игры без русского текста (оригинал)."""
    candidates = [rel_assets + ".bak", rel_assets + ".orig", rel_assets]
    for rel in candidates:
        path = os.path.join(game_dir, rel)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            data = f.read(64 * 1024 * 1024)
        if CYR_MARKER not in data:
            return path
    return os.path.join(game_dir, rel_assets)


def normalize_output(out_dir, source_path):
    """UnityPy пишет файл под именем источника — приводим имя к resources.assets."""
    src_name = os.path.basename(source_path)
    if src_name == "resources.assets":
        return []
    renamed = []
    for name in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path) or not name.startswith(src_name):
            continue
        new_name = "resources.assets" + name[len(src_name):]
        os.replace(path, os.path.join(out_dir, new_name))
        renamed.append((name, new_name))
    return renamed
