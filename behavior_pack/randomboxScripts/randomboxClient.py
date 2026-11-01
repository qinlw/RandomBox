# -*- coding: utf-8 -*-
"""RandomBox 客户端系统

服务端没有“自定义容器界面关闭”事件，而 OpenNeteaseContainer 打开的界面
物品数据需要开发者自行维护。因此这里监听客户端引擎的：

    PushScreenEvent  界面创建
    PopScreenEvent   界面移除

把打开/关闭时机转发给服务端，由服务端在界面刚创建时填充箱子内容，
并在界面关闭后停止从界面回读数据（否则空界面会把箱子内容覆盖成空）。

另外负责测试模式：
    - 服务端通过 rbx_test_state 下发测试开关；
    - 测试按钮与奖池弹窗直接写在容器界面 randombox_container.json 里，
      容器屏打开后用 GetTopUINode 拿到界面节点，SetVisible 显示按钮，
      并用 AddTouchEventParams + SetButtonTouchUpCallback 挂点击回调；
    - 奖池数据由服务端分块下发，缓存后供面板展示 / 搜索。
"""

import mod.client.extraClientApi as clientApi

ClientSystem = clientApi.GetClientSystemCls()

try:
    _STRING_TYPES = (str, unicode)
except NameError:
    _STRING_TYPES = (str,)

MOD_NAMESPACE = "randombox"
SERVER_SYSTEM = "RandomBoxServerSystem"
CLIENT_SYSTEM = "RandomBoxClientSystem"

SCREEN_PREFIX = "randombox_container"
SCREEN_EVENT = "rbx_screen_event"

TEST_STATE_EVENT = "rbx_test_state"
POOL_CHUNK_EVENT = "rbx_test_pool_chunk"
POOL_REQUEST_EVENT = "rbx_test_pool_request"
REFRESH_EVENT = "rbx_test_refresh"
# 服务端通知箱盖开/合
BOX_ANIM_EVENT = "rbx_box_anim"

# 许愿 / 献祭
WISH_SET_EVENT = "rbx_wish_set"
WISH_SAC_EVENT = "rbx_wish_sac"
WISH_STATE_EVENT = "rbx_wish_state"
# 与 JSON / 服务端保持一致：容器 27 格放物品，第 28 格（index 27）是献祭格
CHEST_SLOTS = 27
SAC_SLOT = 27

# 许愿物品选择弹窗：6 × 4 = 24 格图标（与 UI JSON 里的 cell_0..cell_23 对齐）
# 测试模式的「查看奖池」复用同一个弹窗，只读展示
PICK_COLS = 6
PICK_ROWS = 4
PICK_CELLS = PICK_COLS * PICK_ROWS


def _FmtPct(v):
    """百分比数值格式化：去掉多余的尾随 0"""
    try:
        s = ("%.4f" % float(v)).rstrip("0").rstrip(".")
    except Exception:
        s = "0"
    return s or "0"


class RandomBoxClientSystem(ClientSystem):
    def __init__(self, namespace, systemName):
        ClientSystem.__init__(self, namespace, systemName)
        self._testMode = False
        self._boxOpen = False
        self._testNode = None       # 容器屏节点（测试控件所在界面）
        self._testBase = None       # 测试控件路径前缀
        self._pool = []
        self._poolChunks = {}
        self._poolTotal = 0
        # 许愿面板状态
        self._wish = {"item": "", "name": "", "prob": 0.0, "cap": 100}
        self._iconCache = {}       # 物品 ID -> 图标贴图路径
        # 许愿物品选择弹窗（测试模式「查看奖池」复用它，此时为只读）
        self._pickOpen = False
        self._pickViewMode = False
        self._pickPage = 0
        self._pickFilter = ""
        self._pickEntries = []

        engNs = clientApi.GetEngineNamespace()
        engSys = clientApi.GetEngineSystemName()
        handlers = ("PushScreenEvent", "PopScreenEvent", "UiInitFinished")
        for evtName in handlers:
            handler = getattr(self, evtName, None)
            if handler is None:
                continue
            try:
                self.ListenForEvent(engNs, engSys, evtName, self, handler)
            except Exception as e:
                print("[RandomBox] client listen %s error: %s" % (evtName, e))

        for evtName, handler in ((TEST_STATE_EVENT, self.OnTestState),
                                 (POOL_CHUNK_EVENT, self.OnPoolChunk),
                                 (BOX_ANIM_EVENT, self.OnBoxAnim),
                                 (WISH_STATE_EVENT, self.OnWishState)):
            try:
                self.ListenForEvent(MOD_NAMESPACE, SERVER_SYSTEM, evtName,
                                    self, handler)
            except Exception as e:
                print("[RandomBox] client listen %s error: %s" % (evtName, e))
        print("[RandomBox] client system started")

    def _ScreenName(self, args):
        name = args.get("screenName") if isinstance(args, dict) else None
        if isinstance(name, _STRING_TYPES):
            return name
        return ""

    def _NotifyServer(self, action, screenName):
        try:
            self.NotifyToServer(SCREEN_EVENT, {
                "action": action,
                "playerId": clientApi.GetLocalPlayerId(),
                "screenName": screenName,
            })
        except Exception as e:
            print("[RandomBox] notify %s error: %s" % (action, e))

    # ---------- 箱子界面开闭 ----------
    def PushScreenEvent(self, args):
        name = self._ScreenName(args)
        if name.startswith(SCREEN_PREFIX):
            self._boxOpen = True
            self._NotifyServer("push", name)
            # 许愿按钮始终可用，测试按钮仅测试模式
            self._ShowTestUI()

    def PopScreenEvent(self, args):
        name = self._ScreenName(args)
        if name.startswith(SCREEN_PREFIX):
            self._boxOpen = False
            self._NotifyServer("pop", name)
            self._HideTestUI()

    # ---------- 箱盖开合动画 ----------
    def OnBoxAnim(self, args):
        """收到服务端广播后驱动客户端实体播放开/合盖动画"""
        if not isinstance(args, dict):
            return
        try:
            pos = (int(args.get("x")), int(args.get("y")), int(args.get("z")))
        except Exception:
            return
        value = 1.0 if args.get("open") else 0.0
        try:
            comp = clientApi.GetEngineCompFactory().CreateBlockInfo(
                clientApi.GetLevelId())
            comp.SetEnableBlockEntityAnimations(pos, True)
            comp.SetBlockEntityMolangValue(pos, "variable.rbx_open", value)
        except Exception as e:
            print("[RandomBox] box anim error:", e)

    # ---------- 测试面板（控件内嵌在容器屏里，运行时接管） ----------
    def UiInitFinished(self, args):
        pass

    def _ShowTestUI(self):
        try:
            clientApi.GetEngineCompFactory().CreateGame(
                clientApi.GetLevelId()).AddTimer(0.1, self._AttachTestUI)
        except Exception:
            self._AttachTestUI()

    # 容器屏继承了 common 模板，中间层名称不透明；用已知候选名做 BFS，
    # 在整棵控件树里按名字找到 test_buttons，父路径即为 base
    _TREE_NAMES = (
        "variables_button_mappings_and_controls",
        "safezone_screen_matrix", "inner_matrix", "safezone_screen_panel",
        "root_screen_panel", "randombox_panel", "screen_content",
        "content", "content_panel", "root_panel", "main", "panel",
        "container_panel", "base_panel", "body", "root",
    )

    def _FindBase(self, node):
        queue = [""]
        seen = set()
        while queue:
            path = queue.pop(0)
            if path in seen or path.count("/") > 8:
                continue
            seen.add(path)
            try:
                ctrl = node.GetBaseUIControl(path + "/test_buttons")
            except Exception:
                ctrl = None
            if ctrl:
                return path
            parent = None
            if path:
                try:
                    parent = node.GetBaseUIControl(path)
                except Exception:
                    parent = None
                if not parent:
                    continue
            for name in self._TREE_NAMES:
                child = None
                try:
                    if parent is not None:
                        child = parent.GetChildByName(name)
                    else:
                        child = node.GetBaseUIControl("/" + name)
                except Exception:
                    child = None
                if child:
                    queue.append(path + "/" + name)
        return None

    def _AttachTestUI(self):
        if not self._boxOpen:
            return
        try:
            node = clientApi.GetTopUINode()
        except Exception as e:
            print("[RandomBox] GetTopUINode error: %s" % e)
            return
        if not node:
            print("[RandomBox] no top ui node")
            return
        base = self._FindBase(node)
        if base is None:
            print("[RandomBox] test controls not found in top screen")
            return
        print("[RandomBox] test controls found, base=%r" % (base,))
        self._testNode = node
        self._testBase = base
        try:
            node.GetBaseUIControl(base + "/test_buttons").SetVisible(
                bool(self._testMode))
        except Exception as e:
            print("[RandomBox] show test buttons error: %s" % e)
        # 许愿按钮与弹窗始终接线
        self._WireWish(node, base)
        if not self._testMode:
            return
        # 挂按钮回调（button_mappings 已在 JSON 里清空，回调走 API）
        wiring = (
            ("/test_buttons/btn_pool", self._OnBtnPool),
            ("/test_buttons/btn_refresh", self._OnBtnRefresh),
        )
        for path, handler in wiring:
            try:
                btn = node.GetBaseUIControl(base + path).asButton()
                btn.AddTouchEventParams({"isSwallow": True})
                btn.SetButtonTouchUpCallback(handler)
            except Exception as e:
                print("[RandomBox] wire %s error: %s" % (path, e))
        print("[RandomBox] test controls attached, base=%r" % (base,))

    def _HideTestUI(self):
        self._testNode = None
        self._testBase = None

    def OnTestState(self, args):
        enabled = bool(args.get("enabled")) if isinstance(args, dict) else False
        self._testMode = enabled
        if enabled:
            self._pool = []
            self._poolChunks = {}
            self._poolTotal = 0
            if self._boxOpen and self._testNode:
                self._AttachTestUI()
            elif self._boxOpen:
                self._ShowTestUI()
        else:
            node, base = self._testNode, self._testBase
            if node and base is not None:
                try:
                    node.GetBaseUIControl(base + "/test_buttons").SetVisible(False)
                    self._ClosePick()
                except Exception:
                    pass
        print("[RandomBox] test mode: %s" % enabled)

    # ---------- 测试按钮回调 ----------
    def _OnBtnPool(self, args):
        """查看奖池：复用许愿选择弹窗（只读模式）"""
        self._OpenPick(True)

    def _OnBtnRefresh(self, args):
        self.RequestRefresh()

    # ---------- 奖池弹窗内容 ----------
    def _SetLabel(self, path, text):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        try:
            node.GetBaseUIControl(base + path).asLabel().SetText(text)
        except Exception as e:
            print("[RandomBox] set label %s error: %s" % (path, e))

    # ---------- 奖池数据 ----------
    def OnPoolChunk(self, args):
        if not isinstance(args, dict):
            return
        try:
            index = int(args.get("index", 0))
            total = int(args.get("total", 1))
        except Exception:
            return
        items = args.get("items") or []
        if not isinstance(items, list):
            return
        self._poolChunks[index] = items
        self._poolTotal = total
        if len(self._poolChunks) < total:
            return
        pool = []
        for i in range(total):
            pool.extend(self._poolChunks.get(i, []))
        self._pool = pool
        self._poolChunks = {}
        print("[RandomBox] pool received items=%d" % len(pool))
        self.RefreshPick()

    def GetPool(self):
        return self._pool

    def RequestPool(self):
        try:
            self.NotifyToServer(POOL_REQUEST_EVENT, {
                "playerId": clientApi.GetLocalPlayerId()})
        except Exception as e:
            print("[RandomBox] request pool error: %s" % e)

    def RequestRefresh(self):
        try:
            self.NotifyToServer(REFRESH_EVENT, {
                "playerId": clientApi.GetLocalPlayerId()})
        except Exception as e:
            print("[RandomBox] request refresh error: %s" % e)

    # ---------- 许愿 / 献祭面板 ----------
    def _WireWish(self, node, base):
        """许愿面板与献祭按钮接线（与测试模式无关，始终生效）"""
        wiring = [
            ("/wish_panel/btn_wish", self._OnBtnWish),
            ("/wish_panel/btn_sacrifice", self._OnBtnSacrifice),
            ("/pick_dialog/btn_close", self._OnBtnPickClose),
            ("/pick_dialog/btn_search", self._OnBtnPickSearch),
            ("/pick_dialog/btn_prev", self._OnBtnPickPrev),
            ("/pick_dialog/btn_next", self._OnBtnPickNext),
        ]
        for i in range(PICK_CELLS):
            wiring.append(("/pick_dialog/cell_%d" % i, self._PickHandler(i)))
        for path, handler in wiring:
            try:
                btn = node.GetBaseUIControl(base + path).asButton()
                btn.AddTouchEventParams({"isSwallow": True})
                btn.SetButtonTouchUpCallback(handler)
            except Exception as e:
                print("[RandomBox] wire wish %s error: %s" % (path, e))
        print("[RandomBox] wish controls attached")
        self._SetGridVisible(True)
        self.RefreshWish()

    def _ShowControl(self, path, show):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        try:
            node.GetBaseUIControl(base + path).SetVisible(bool(show))
        except Exception:
            pass

    # 物品格的数量角标属于 container_item 模板的深层控件，层级高于弹窗，
    # 打开弹窗时直接隐藏物品网格，避免「数字浮在弹窗上」
    GRID_PATHS = (
        "/randombox_panel/root_panel/common_panel/bg_image/chest_panel"
        "/randombox_panel_top_half/randombox_grid1",
    )

    def _SetGridVisible(self, show):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        for path in self.GRID_PATHS:
            try:
                ctrl = node.GetBaseUIControl(base + path)
            except Exception:
                ctrl = None
            if not ctrl:
                continue
            try:
                ctrl.SetVisible(bool(show))
            except Exception as e:
                print("[RandomBox] grid visible error: %s" % e)
            return
        print("[RandomBox] grid control not found: %s" % (self.GRID_PATHS[0],))

    # ---------- 许愿物品选择弹窗 ----------
    def _OnBtnWish(self, args):
        self._OpenPick()

    def _OpenPick(self, viewMode=False):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        self._pickOpen = True
        self._pickViewMode = bool(viewMode)
        self._pickPage = 0
        self._pickFilter = ""
        try:
            node.GetBaseUIControl(
                base + "/pick_dialog/search_box").asTextEditBox().SetEditText("")
        except Exception:
            pass
        try:
            node.GetBaseUIControl(base + "/pick_dialog").SetVisible(True)
        except Exception as e:
            print("[RandomBox] open pick error: %s" % e)
        # 隐藏物品网格：否则格子里的数量角标会浮在弹窗上面
        self._SetGridVisible(False)
        if not self._pool:
            self.RequestPool()
        self.RefreshPick()

    def _ClosePick(self):
        node, base = self._testNode, self._testBase
        self._pickOpen = False
        self._pickViewMode = False
        self._SetGridVisible(True)
        if not node or base is None:
            return
        try:
            node.GetBaseUIControl(base + "/pick_dialog").SetVisible(False)
        except Exception:
            pass

    def _OnBtnPickClose(self, args):
        self._ClosePick()

    def _OnBtnPickSearch(self, args):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        text = ""
        try:
            text = node.GetBaseUIControl(
                base + "/pick_dialog/search_box").asTextEditBox().GetEditText()
        except Exception as e:
            print("[RandomBox] read pick search error: %s" % e)
        self._pickFilter = (text or "").strip()
        self._pickPage = 0
        self.RefreshPick()

    def _OnBtnPickPrev(self, args):
        if self._pickPage > 0:
            self._pickPage -= 1
            self.RefreshPick()

    def _OnBtnPickNext(self, args):
        self._pickPage += 1
        self.RefreshPick()

    def _PickHandler(self, index):
        def handler(args):
            if self._pickViewMode:
                return
            pos = self._pickPage * PICK_CELLS + index
            if pos >= len(self._pickEntries):
                return
            iid = self._pickEntries[pos][0]
            try:
                self.NotifyToServer(WISH_SET_EVENT, {
                    "playerId": clientApi.GetLocalPlayerId(), "item": iid})
            except Exception as e:
                print("[RandomBox] wish set error: %s" % e)
            self._ClosePick()
        return handler

    def RefreshPick(self):
        if not self._pickOpen:
            return
        if not self._pool:
            self._pickEntries = []
        else:
            kw = self._pickFilter.lower()
            entries = []
            for it in self._pool:
                if isinstance(it, dict):
                    iid = it.get("id") or ""
                    cn = it.get("name") or ""
                else:
                    iid, cn = str(it), ""
                if not iid:
                    continue
                if kw and kw not in iid.lower() and kw not in cn.lower():
                    continue
                entries.append((iid, cn))
            self._pickEntries = entries
        total = len(self._pickEntries)
        pages = max(1, (total + PICK_CELLS - 1) // PICK_CELLS)
        if self._pickPage >= pages:
            self._pickPage = pages - 1
        start = self._pickPage * PICK_CELLS
        for i in range(PICK_CELLS):
            pos = start + i
            iid = self._pickEntries[pos][0] if pos < total else ""
            if iid:
                self._ShowControl("/pick_dialog/cell_%d/icon" % i, True)
                self._SetIcon("/pick_dialog/cell_%d/icon" % i, iid)
            else:
                self._ShowControl("/pick_dialog/cell_%d/icon" % i, False)
        self._SetLabel("/pick_dialog/title_label",
                       ("奖池：%d 个物品" if self._pickViewMode
                        else "选择许愿物品（%d）") % total)
        self._SetLabel("/pick_dialog/page_label",
                       "第 %d/%d 页" % (self._pickPage + 1, pages))
        if not self._pool:
            self._SetLabel("/pick_dialog/hint_label", "奖池加载中…")
        elif total == 0:
            self._SetLabel("/pick_dialog/hint_label", "没有匹配的物品")
        elif self._pickViewMode:
            self._SetLabel("/pick_dialog/hint_label", "只读查看，点「关闭」返回")
        else:
            self._SetLabel("/pick_dialog/hint_label", "点图标即设为许愿物品")

    # ---- 物品图标 ----
    def _ItemIcon(self, name):
        """把物品 ID 解析成贴图路径；取不到时返回空串（控件隐藏）"""
        if not name:
            return ""
        if name in self._iconCache:
            return self._iconCache[name]
        factory = clientApi.GetEngineCompFactory()
        tex = ""
        for entId in (clientApi.GetLocalPlayerId(), clientApi.GetLevelId()):
            comp = None
            try:
                comp = factory.CreateItem(entId)
            except Exception:
                continue
            if comp is None:
                continue
            for call in (lambda: comp.GetItemTexture(name, 0),
                         lambda: comp.GetItemTexture(name)):
                try:
                    got = call()
                except Exception:
                    got = None
                if isinstance(got, _STRING_TYPES) and got:
                    tex = got
                    break
            if tex:
                break
            try:
                info = comp.GetItemBasicInfo(name, 0)
            except Exception:
                info = None
            if isinstance(info, dict):
                for key in ("iconPath", "texture", "texturePath"):
                    val = info.get(key)
                    if isinstance(val, _STRING_TYPES) and val:
                        tex = val
                        break
            if tex:
                break
        if tex and not tex.startswith("textures/"):
            tex = "textures/" + tex
        self._iconCache[name] = tex
        return tex

    def _SetIcon(self, path, name, show_if_missing=False):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        tex = self._ItemIcon(name)
        if not tex:
            self._ShowControl(path, show_if_missing)
            return
        try:
            self._ShowControl(path, True)
            node.GetBaseUIControl(base + path).asImage().SetSprite(tex)
        except Exception as e:
            print("[RandomBox] set icon %s error: %s" % (path, e))

    # ---- 面板按钮 ----
    def _OnBtnSacrifice(self, args):
        if not (self._wish.get("item") or ""):
            self._SetLabel("/wish_panel/wish_name_label", "先选许愿物品")
            return
        try:
            self.NotifyToServer(WISH_SAC_EVENT, {
                "playerId": clientApi.GetLocalPlayerId()})
        except Exception as e:
            print("[RandomBox] sacrifice error: %s" % e)

    # ---- 服务端数据下发 ----
    def OnWishState(self, args):
        if not isinstance(args, dict):
            return
        self._wish = {
            "item": args.get("item") or "",
            "name": args.get("name") or "",
            "prob": args.get("prob") or 0.0,
            "cap": args.get("cap", 100),
        }
        self.RefreshWish()

    # ---- 面板刷新 ----
    def RefreshWish(self):
        if not self._testNode or self._testBase is None:
            return
        w = self._wish
        item = w.get("item") or ""
        if item:
            self._SetLabel("/wish_panel/wish_name_label",
                           w.get("name") or item)
            self._SetIcon("/wish_panel/btn_wish/icon", item)
        else:
            self._SetLabel("/wish_panel/wish_name_label", "点图标选许愿")
            self._ShowControl("/wish_panel/btn_wish/icon", False)
        try:
            cap = int(w.get("cap", 100))
        except Exception:
            cap = 100
        self._SetLabel("/wish_panel/prob_label",
                       "累计 %s％" % _FmtPct(w.get("prob", 0.0)))
        self._SetLabel("/wish_panel/cap_label", "上限 %d％" % cap)
