# -*- coding: utf-8 -*-
"""RandomBox 打包脚本

产物命名与 RandomPocket 保持一致：
    RandomBox.mcaddon    可直接导入游戏
    RandomBox.zip        平台导入用的压缩包

用法：python build.py
"""

import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
MCADDON = os.path.join(ROOT, "RandomBox.mcaddon")
ZIP = os.path.join(ROOT, "RandomBox.zip")

PACKS = ("behavior_pack", "resource_pack")
SKIP_DIRS = ("__pycache__",)
SKIP_EXTS = (".pyc", ".pyo")


def build(target):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for pack in PACKS:
            base = os.path.join(ROOT, pack)
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in filenames:
                    if name.endswith(SKIP_EXTS):
                        continue
                    full = os.path.join(dirpath, name)
                    arc = pack + "/" + os.path.relpath(full, base).replace(os.sep, "/")
                    z.write(full, arc)
    print("built %s (%d bytes)" % (os.path.basename(target), os.path.getsize(target)))


def check():
    """包结构自检：根目录仅行为包/资源包，且各自必备目录存在"""
    with zipfile.ZipFile(MCADDON) as z:
        names = z.namelist()
    roots = sorted(set(n.split("/")[0] for n in names))
    assert roots == list(PACKS), "根目录必须仅有 %s，实际 %s" % (PACKS, roots)
    assert any(n.startswith("behavior_pack/entities/") for n in names), "行为包缺少 entities"
    assert any(n.startswith("resource_pack/textures/") for n in names), "资源包缺少 textures"
    print("structure ok")


if __name__ == "__main__":
    build(MCADDON)
    build(ZIP)
    check()
