# -*- coding: utf-8 -*-
"""剪映草稿加解密（ctypes 调用剪映 videoeditor.dll 内部 EncryptUtils）

【本文件是从 AutoTool 的 autotool/jianying/jy_crypto.py 原样搬入的副本】
2026-07-27 用户裁定：短剧创作项目要能独立跑，不依赖 AutoTool 的代码。
只依赖标准库 + 剪映自带 DLL；DLL 目录从 工具/剪映/剪映配置.json 的
`剪映安装目录` 读（见 _config_install_dir），换版本改那里。
上游若有修复，整文件重新覆盖即可，不要在这里改逻辑。

剪映 Windows 端草稿（draft_content.json / draft_meta_info.json）采用
jianying_draft_encrypt_v2 加密。算法不公开，只能复用剪映自带 DLL。

本模块在进程内用 ctypes 复刻 jy-draftc 的 MSVC std::string ABI 调用，
对外只暴露：
    decrypt(data)            密文 -> 明文
    encrypt(text)            明文 -> 密文（用于自校验/回写，本流程默认不回加密）
    looks_encrypted(data)    判断一段内容是否为密文
    load_text(path)          读文件，自动判别明文/密文并返回明文 str
不在 UI 暴露；core 读取草稿时自动调用。

DLL 路径解析顺序：
    1. set_install_dir() 显式设置 / 参数传入
    2. 环境变量 JY_INSTALL_DIR
    3. 配置文件 工具/剪映/剪映配置.json -> 剪映安装目录
       （兼容 AutoTool 的 config/jianying_config.json -> jy_install_dir）
    4. 自动探测常见安装目录下最高版本
    5. 内置常量 DEFAULT_INSTALL_DIR 兜底

JY_INSTALL_DIR 要指向【包含 videoeditor.dll 的那个版本目录】，例如：
    F:\\剪映\\JianyingPro\\10.7.0.14095
"""
import os
import re
import json
import ctypes
import logging
from pathlib import Path

log = logging.getLogger("jianying.jy_crypto")

# 内置兜底常量（本机已确认存在）
DEFAULT_INSTALL_DIR = r"F:\剪映\JianyingPro\10.7.0.14095"

# 自动探测的搜索根
_SEARCH_ROOTS = [
    r"F:\剪映\JianyingPro", r"E:\剪映\JianyingPro", r"D:\剪映\JianyingPro",
    r"C:\Program Files\JianyingPro", r"D:\JianyingPro", r"E:\JianyingPro",
    r"F:\JianyingPro",
    os.path.expandvars(r"%LOCALAPPDATA%\JianyingPro\Apps"),
]

# 已验证的导出符号（10.3.0 ~ 10.7.0 一致）
_SYM_DECRYPT = (
    "?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z")
_SYM_ENCRYPT = (
    "?encrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@@Z")
_SYM_ENABLE = "?enable@EncryptUtils@lvve@@QEAAX_N@Z"

_explicit_install_dir = None  # set_install_dir() 设置


# ============================================================
# MSVC std::string ABI（与 jy-draftc 的 MsvcString 一致，x64）
# ============================================================
class _Data(ctypes.Union):
    _fields_ = [("small", ctypes.c_char * 16), ("ptr", ctypes.c_void_p)]


class MsvcString(ctypes.Structure):
    _fields_ = [("data", _Data),
                ("size", ctypes.c_ulonglong),
                ("capacity", ctypes.c_ulonglong)]


def _make_in(b: bytes, keepalive: list) -> MsvcString:
    m = MsvcString()
    n = len(b)
    m.size = n
    if n < 16:
        m.capacity = 15
        ctypes.memmove(m.data.small, b, n)
    else:
        buf = ctypes.create_string_buffer(b, n + 1)  # 末尾留 NUL，容量=n
        keepalive.append(buf)
        m.capacity = n
        m.data.ptr = ctypes.cast(buf, ctypes.c_void_p)
    return m


def _take(m: MsvcString) -> bytes:
    if m.capacity < 16:
        return bytes(m.data.small[:m.size])
    if not m.data.ptr or m.size > (1 << 32):
        return b""
    return ctypes.string_at(m.data.ptr, m.size)


# ============================================================
# DLL 路径解析
# ============================================================
def set_install_dir(path):
    """显式指定剪映版本目录（含 videoeditor.dll）。"""
    global _explicit_install_dir, _api
    _explicit_install_dir = str(path) if path else None
    _api = None  # 触发重新加载


def _config_install_dir():
    """先读本项目的 工具/剪映/剪映配置.json，再兼容 AutoTool 的老配置。"""
    candidates = [
        (Path(__file__).resolve().parent / "剪映配置.json", "剪映安装目录"),
        (Path(__file__).resolve().parents[2] / "config" / "jianying_config.json", "jy_install_dir"),
    ]
    for cfg, key in candidates:
        try:
            if cfg.exists():
                d = json.loads(cfg.read_text(encoding="utf-8-sig")).get(key)
                if d:
                    return d
        except Exception:
            continue
    return None


def _version_key(name: str):
    nums = re.findall(r"\d+", name)
    return [int(x) for x in nums] if nums else [0]


def _auto_detect():
    for root in _SEARCH_ROOTS:
        try:
            rp = Path(root)
            if not rp.is_dir():
                continue
            cands = [d for d in rp.iterdir() if d.is_dir() and (d / "videoeditor.dll").exists()]
            if cands:
                best = max(cands, key=lambda d: _version_key(d.name))
                return str(best)
        except Exception:
            continue
    return None


def resolve_install_dir() -> str:
    for src in (_explicit_install_dir,
                os.environ.get("JY_INSTALL_DIR"),
                _config_install_dir(),
                _auto_detect(),
                DEFAULT_INSTALL_DIR):
        if src and (Path(src) / "videoeditor.dll").exists():
            return str(src)
    raise FileNotFoundError(
        "找不到 videoeditor.dll。请在 config/jianying_config.json 设置 "
        "jy_install_dir 指向剪映版本目录（含 videoeditor.dll），"
        f"例如：{DEFAULT_INSTALL_DIR}")


# ============================================================
# DLL 加载与函数绑定（懒加载 + 缓存）
# ============================================================
class _Api:
    def __init__(self, install_dir: str):
        self.install_dir = install_dir
        dll_path = Path(install_dir) / "videoeditor.dll"
        # 让同目录依赖 DLL 可被解析
        try:
            os.add_dll_directory(install_dir)
        except Exception:
            pass
        os.environ["PATH"] = install_dir + os.pathsep + os.environ.get("PATH", "")
        self._dll = ctypes.WinDLL(str(dll_path))

        gpa = ctypes.windll.kernel32.GetProcAddress
        gpa.restype = ctypes.c_void_p
        gpa.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

        def bind(sym, restype, argtypes):
            addr = gpa(self._dll._handle, sym.encode("ascii"))
            if not addr:
                raise OSError(f"导出符号缺失: {sym[:40]}...")
            return ctypes.CFUNCTYPE(restype, *argtypes)(addr)

        P = ctypes.POINTER(MsvcString)
        self._decrypt = bind(_SYM_DECRYPT, ctypes.c_void_p,
                             [ctypes.c_void_p, P, P, P, ctypes.POINTER(ctypes.c_bool)])
        self._encrypt = bind(_SYM_ENCRYPT, ctypes.c_void_p,
                             [ctypes.c_void_p, P, P])
        self._enable = bind(_SYM_ENABLE, None, [ctypes.c_void_p, ctypes.c_bool])

    def decrypt(self, data: bytes) -> bytes:
        keep = []
        in_ = _make_in(data, keep)
        param = _make_in(b"{}", keep)
        out = MsvcString()
        ok = ctypes.c_bool(False)
        self._decrypt(None, ctypes.byref(out), ctypes.byref(in_),
                      ctypes.byref(param), ctypes.byref(ok))
        if not ok.value:
            raise RuntimeError("解密失败（ok=false），可能版本不匹配或非加密内容")
        return _take(out)

    def encrypt(self, data: bytes) -> bytes:
        self._enable(None, True)
        keep = []
        in_ = _make_in(data, keep)
        out = MsvcString()
        self._encrypt(None, ctypes.byref(out), ctypes.byref(in_))
        return _take(out)


_api = None


def _get_api() -> _Api:
    global _api
    if _api is None:
        _api = _Api(resolve_install_dir())
        log.info("videoeditor.dll 已加载: %s", _api.install_dir)
    return _api


# ============================================================
# 对外接口
# ============================================================
def looks_encrypted(data) -> bool:
    """明文 JSON 去掉 BOM/空白后以 { 或 [ 开头；否则视为密文。"""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    i = 0
    if data[:3] == b"\xef\xbb\xbf":
        i = 3
    while i < len(data) and data[i] in (0x20, 0x09, 0x0d, 0x0a):
        i += 1
    return not (i < len(data) and data[i] in (0x7b, 0x5b))  # { [


def decrypt(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _get_api().decrypt(data).decode("utf-8", errors="replace")


def encrypt(text) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return _get_api().encrypt(text).decode("latin-1")


def load_text(path) -> str:
    """读取草稿 JSON 文件，自动判别明文/密文，返回明文字符串。"""
    raw = Path(path).read_bytes()
    if looks_encrypted(raw):
        return decrypt(raw)
    return raw.decode("utf-8-sig", errors="replace")


def decrypt_file_inplace(path) -> bool:
    """原地解密一个草稿 JSON 文件。已是明文则跳过。
    返回 True 表示发生了解密，False 表示原本就是明文。"""
    p = Path(path)
    raw = p.read_bytes()
    if not looks_encrypted(raw):
        return False
    plain = decrypt(raw)
    p.write_text(plain, encoding="utf-8")
    return True


# 草稿目录里需要降级（解密）的文件
DRAFT_JSON_FILES = ("draft_content.json", "draft_meta_info.json")


def decrypt_draft_inplace(draft_dir) -> dict:
    """原地解密一个草稿文件夹下的 draft_content.json / draft_meta_info.json。
    返回 {文件名: 'decrypted'|'plain'|'missing'}。"""
    dd = Path(draft_dir)
    result = {}
    for fn in DRAFT_JSON_FILES:
        fp = dd / fn
        if not fp.exists():
            result[fn] = "missing"
        else:
            result[fn] = "decrypted" if decrypt_file_inplace(fp) else "plain"
    return result


def is_draft_dir(path) -> bool:
    return (Path(path) / "draft_content.json").exists()


def draft_is_encrypted(draft_dir) -> bool:
    """草稿的 draft_content.json 是否为加密格式。"""
    p = Path(draft_dir) / "draft_content.json"
    if not p.exists():
        return False
    try:
        return looks_encrypted(p.read_bytes())
    except Exception:
        return False
