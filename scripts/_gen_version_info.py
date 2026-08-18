"""从 version.py 生成 packaging/version_info.txt(EXE 内嵌版本资源)。
版本唯一源是 version.py — 禁止在本脚本写死版本号。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from version import APP_NAME, APP_NAME_EN, APP_VERSION  # noqa: E402

nums = [int(p) for p in APP_VERSION.split(".")]
while len(nums) < 4:
    nums.append(0)

template = f"""# UTF-8
# 由 scripts/_gen_version_info.py 从 version.py 动态生成 — 不要手改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple(nums)},
    prodvers={tuple(nums)},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404B0',
        [StringStruct(u'CompanyName', u'{APP_NAME_EN}'),
         StringStruct(u'FileDescription', u'{APP_NAME}'),
         StringStruct(u'FileVersion', u'{APP_VERSION}'),
         StringStruct(u'InternalName', u'{APP_NAME}'),
         StringStruct(u'OriginalFilename', u'{APP_NAME}.exe'),
         StringStruct(u'ProductName', u'{APP_NAME}'),
         StringStruct(u'ProductVersion', u'{APP_VERSION}')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""
out = Path(__file__).resolve().parent.parent / "packaging" / "version_info.txt"
out.write_text(template, encoding="utf-8")
print(f"version_info.txt → {APP_VERSION}")
