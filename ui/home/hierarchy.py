"""Two-level category metadata shared by storage and the desktop UI."""
import re


def validate_category_name(name):
    name = name.strip()
    if (not name or len(name) > 64 or name == '未分类' or name.endswith(('.', ' '))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I)):
        raise ValueError('分类名称需要 1～64 个有效文件夹字符，“未分类”为系统默认分类。')
    return name


def validate_hierarchy(categories, parents):
    if not isinstance(parents, dict):
        raise ValueError('分类层级数据无效。')
    for key, parent in parents.items():
        if (key not in categories or not isinstance(parent, str) or not parent
                or parent not in categories or parent == key or parents.get(parent)):
            raise ValueError('分类最多支持两级，请重新选择一级分类。')
    seen = set()
    for key, name in categories.items():
        pair = (parents.get(key, ''), name.casefold())
        if pair in seen:
            raise ValueError('同一级分类下已有同名分类。')
        seen.add(pair)


def category_rows(categories, parents):
    """Keep insertion order within siblings, with each root before its children."""
    for key, name in categories.items():
        if not parents.get(key):
            yield key, name, 0
            for child, child_name in categories.items():
                if parents.get(child) == key:
                    yield child, child_name, 1


def populate_category_menu(menu, library, callback, selected=()):
    """Use the same destinations for a single note and batch classification."""
    selected = set(selected)

    def add(target, key, label):
        action = target.addAction(label)
        action.setData(key)
        action.setCheckable(True)
        action.setChecked(selected == {key})
        action.triggered.connect(lambda checked=False, key=key: callback(key))

    add(menu, '', '未分类')
    if library.categories:
        menu.addSeparator()
    for key, name, depth in library.category_rows():
        if depth:
            continue
        children = library.child_categories(key)
        if children:
            branch = menu.addMenu(name)
            add(branch, key, '移入「' + name + '」')
            branch.addSeparator()
            for child in children:
                add(branch, child, library.categories[child])
        else:
            add(menu, key, name)
