"""本地登记文档与受控引用闭包。

只解析登记目录内的 JSON 文档与文档内引用；外部 URL、绝对路径与越界相对路径
一律拒绝，不发生任何网络访问。
"""
import json
from pathlib import Path
import posixpath
from urllib.parse import urlsplit

class RegistryError(ValueError):
    """引用不在允许闭包内或指针无法解析。"""

def _unescape(token):
    return token.replace('~1', '/').replace('~0', '~')

def _escape_pointer(token):
    return str(token).replace('~', '~0').replace('/', '~1')

class Registry:
    def __init__(self):
        self._documents = {}

    @classmethod
    def from_directory(cls, directory):
        registry = cls()
        root = Path(directory).resolve()
        if not root.is_dir():
            raise RegistryError('登记目录不存在: ' + str(directory))
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.suffix.lower() == '.json':
                key = path.relative_to(root).as_posix()
                with path.open(encoding='utf-8') as handle:
                    registry.add(key, json.load(handle))
        if not registry._documents:
            raise RegistryError('登记目录内没有 JSON 文档')
        return registry

    def add(self, key, document):
        key = self._safe_key(key)
        if not isinstance(document, (dict, list)):
            raise RegistryError('登记文档必须是 JSON 对象或数组: ' + key)
        self._documents[key] = document

    @staticmethod
    def _safe_key(key):
        key = str(key).replace('\\', '/')
        if not key or key.startswith('/') or posixpath.normpath(key).startswith('..'):
            raise RegistryError('登记键必须是目录内相对路径: ' + str(key))
        if urlsplit(key).scheme:
            raise RegistryError('登记键不能带 URI scheme: ' + key)
        return posixpath.normpath(key)

    @property
    def keys(self):
        return sorted(self._documents)

    def document(self, key):
        try:
            return self._documents[key]
        except KeyError:
            raise RegistryError('未登记文档: ' + key)

    def resolve(self, ref, current_key):
        """解析完整 $ref，返回 (目标文档键, 指针, 目标节点)。"""
        if not isinstance(ref, str) or not ref:
            raise RegistryError('$ref 必须是非空字符串')
        uri, separator, fragment = ref.partition('#')
        split = urlsplit(uri)
        if split.scheme or split.netloc:
            raise RegistryError('只允许本地登记引用，拒绝外部引用: ' + ref)
        if uri in ('', current_key):
            target_key = current_key
        else:
            if uri.startswith('/'):
                raise RegistryError('拒绝绝对路径引用: ' + ref)
            target_key = posixpath.normpath(posixpath.join(posixpath.dirname(current_key), uri))
            if target_key.startswith('..'):
                raise RegistryError('引用越出登记目录: ' + ref)
            if target_key not in self._documents:
                raise RegistryError('引用的文档未登记: ' + ref)
        if separator and fragment and not fragment.startswith('/'):
            raise RegistryError('只支持 JSON 指针片段: ' + ref)
        node = self._documents[target_key]
        pointer = fragment if fragment.startswith('/') else ''
        if pointer:
            for raw_token in pointer.split('/')[1:]:
                token = _unescape(raw_token)
                if isinstance(node, list):
                    try:
                        index = int(token)
                    except ValueError:
                        raise RegistryError('数组指针不是整数: ' + ref)
                    if not 0 <= index < len(node):
                        raise RegistryError('指针越界: ' + ref)
                    node = node[index]
                elif isinstance(node, dict):
                    if token not in node:
                        raise RegistryError('指针目标不存在: ' + ref)
                    node = node[token]
                else:
                    raise RegistryError('指针无法穿透标量: ' + ref)
        return target_key, pointer, node

    def closure(self, root_key):
        """返回根文档可达的全部去重 $ref（已校验可解析），排序保证确定性。

        不可解析/越界引用不抛异常（计划器将其转为阻塞诊断），只收集可解析项。
        """
        if root_key not in self._documents:
            raise RegistryError('未登记文档: ' + root_key)
        seen = set()
        ordered = []

        def walk(node, current_key):
            if isinstance(node, dict):
                if '$ref' in node:
                    ref = node['$ref']
                    if ref not in seen:
                        seen.add(ref)
                        try:
                            target_key, _, target = self.resolve(ref, current_key)
                        except RegistryError:
                            return
                        ordered.append(ref)
                        walk(target, target_key)
                    return
                for key in sorted(node):
                    walk(node[key], current_key)
            elif isinstance(node, list):
                for item in node:
                    walk(item, current_key)

        walk(self._documents[root_key], root_key)
        return sorted(ordered)
