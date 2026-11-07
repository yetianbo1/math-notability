"""GitHub release metadata and the application's shared version ordering."""
from dataclasses import dataclass, replace
import json
from urllib.parse import quote, unquote, urlsplit, parse_qs
from xml.etree import ElementTree

from core.ne_version import version_key

REPOSITORY = 'SeedStudent/NotabilityEditor'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases'
FEED_URL = RELEASES_URL + '.atom'
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    version: str
    key: tuple
    url: str
    notes: str
    prerelease: bool
    complete: bool = True


def release_page(data):
    """Ignore drafts and unrelated tags, including no executable release titles."""
    values = json.loads(data)
    if not isinstance(values, list):
        raise ValueError('发布接口返回了无效数据。')
    if len(values) > PAGE_SIZE:
        raise ValueError('发布列表超出预期范围。')
    releases = []
    for value in values:
        if not isinstance(value, dict) or value.get('draft', False):
            continue
        tag = value.get('tag_name')
        if not isinstance(tag, str):
            continue
        version = tag[1:] if tag.startswith(('v', 'V')) else tag
        try:
            key = version_key(version)
        except ValueError:
            continue
        # Build links ourselves; never open untrusted URLs returned in metadata.
        url = RELEASES_URL + '/tag/' + quote(tag, safe='')
        notes = value.get('body')
        notes = notes[:30000] if isinstance(notes, str) and notes.strip() else '该版本未提供更新说明。'
        releases.append(Release(version, key, url, notes, bool(value.get('prerelease'))))
    return releases


def has_next_page(link):
    # Page URLs are constructed locally instead of following header URLs.
    return any('rel="next"' in part or "rel='next'" in part
               for part in link.split(','))


def release_feed(data):
    """Read only this repository's official Atom feed, never HTML error pages."""
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('发布订阅包含无效内容。')
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError('发布订阅返回了无效数据。') from exc
    atom = '{http://www.w3.org/2005/Atom}'
    if root.tag != atom + 'feed':
        raise ValueError('发布订阅格式不正确。')
    identity = root.findtext(atom + 'id', '')
    if identity != 'tag:github.com,2008:' + RELEASES_URL:
        raise ValueError('发布订阅不属于当前项目。')
    releases = []
    prefix = f'/{REPOSITORY}/releases/tag/'
    for entry in root.findall(atom + 'entry'):
        for link in entry.findall(atom + 'link'):
            if link.get('rel', 'alternate') != 'alternate':
                continue
            parsed = urlsplit(link.get('href', ''))
            if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
                    or not parsed.path.startswith(prefix) or parsed.query or parsed.fragment):
                continue
            tag = unquote(parsed.path[len(prefix):])
            releases.extend(replace(release, complete=False)
                            for release in release_page(json.dumps([{'tag_name': tag}])))
            break
    next_page = None
    for link in root.findall(atom + 'link'):
        if link.get('rel') != 'next':
            continue
        parsed = urlsplit(link.get('href', ''))
        query = parse_qs(parsed.query)
        page = query.get('page', [''])[0]
        if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
                or parsed.path != f'/{REPOSITORY}/releases.atom'
                or not page.isdecimal() or len(page) > 4):
            raise ValueError('发布订阅分页无效。')
        next_page = int(page)
    return releases, next_page


def official_release_url(value):
    parsed = urlsplit(value)
    return (parsed.scheme == 'https' and parsed.netloc == 'github.com'
            and (parsed.path == f'/{REPOSITORY}/releases'
                 or parsed.path.startswith(f'/{REPOSITORY}/releases/tag/')))
