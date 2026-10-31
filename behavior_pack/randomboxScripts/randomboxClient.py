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

PAGE_SIZE = 12


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
        self._poolFilter = ""
        self._poolPage = 0

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
                                 (BOX_ANIM_EVENT, self.OnBoxAnim)):
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
            if self._testMode:
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
        if not self._testMode:
            return
        # 挂按钮回调（button_mappings 已在 JSON 里清空，回调走 API）
        wiring = (
            ("/test_buttons/btn_pool", self._OnBtnPool),
            ("/test_buttons/btn_refresh", self._OnBtnRefresh),
            ("/pool_dialog/btn_search", self._OnBtnSearch),
            ("/pool_dialog/btn_prev", self._OnBtnPrev),
            ("/pool_dialog/btn_next", self._OnBtnNext),
            ("/pool_dialog/btn_close", self._OnBtnClose),
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
        self._poolFilter = ""
        self._poolPage = 0

    def OnTestState(self, args):
        enabled = bool(args.get("enabled")) if isinstance(args, dict) else False
        self._testMode = enabled
        if enabled:
            self._pool = []
            self._poolChunks = {}
            self._poolTotal = 0
            self._poolFilter = ""
            self._poolPage = 0
            if self._boxOpen and self._testNode:
                self._AttachTestUI()
            elif self._boxOpen:
                self._ShowTestUI()
        else:
            node, base = self._testNode, self._testBase
            if node and base is not None:
                try:
                    node.GetBaseUIControl(base + "/test_buttons").SetVisible(False)
                    node.GetBaseUIControl(base + "/pool_dialog").SetVisible(False)
                except Exception:
                    pass
        print("[RandomBox] test mode: %s" % enabled)

    # ---------- 测试按钮回调 ----------
    def _OnBtnPool(self, args):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        try:
            node.GetBaseUIControl(base + "/pool_dialog").SetVisible(True)
        except Exception as e:
            print("[RandomBox] open pool dialog error: %s" % e)
        if not self._pool:
            self.RequestPool()
        self.RefreshPool()

    def _OnBtnRefresh(self, args):
        self.RequestRefresh()

    def _OnBtnSearch(self, args):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        text = ""
        try:
            text = node.GetBaseUIControl(
                base + "/pool_dialog/search_box").asTextEditBox().GetEditText()
        except Exception as e:
            print("[RandomBox] read search box error: %s" % e)
        self._poolFilter = (text or "").strip()
        self._poolPage = 0
        self.RefreshPool()

    def _OnBtnPrev(self, args):
        if self._poolPage > 0:
            self._poolPage -= 1
            self.RefreshPool()

    def _OnBtnNext(self, args):
        self._poolPage += 1
        self.RefreshPool()

    def _OnBtnClose(self, args):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        try:
            node.GetBaseUIControl(base + "/pool_dialog").SetVisible(False)
        except Exception:
            pass

    # ---------- 奖池弹窗内容 ----------
    def _SetLabel(self, path, text):
        node, base = self._testNode, self._testBase
        if not node or base is None:
            return
        try:
            node.GetBaseUIControl(base + path).asLabel().SetText(text)
        except Exception as e:
            print("[RandomBox] set label %s error: %s" % (path, e))

    def RefreshPool(self):
        if not self._testNode or self._testBase is None:
            return
        entries = []
        for it in self._pool:
            if isinstance(it, dict):
                iid = it.get("id") or ""
                cn = it.get("name") or ""
            else:
                iid, cn = str(it), ""
            entries.append((iid, cn))
        kw = self._poolFilter.lower()
        if kw:
            entries = [e for e in entries
                       if kw in e[0].lower() or kw in e[1].lower()]
        total = len(self._pool)
        matched = len(entries)
        pages = max(1, (matched + PAGE_SIZE - 1) // PAGE_SIZE)
        if self._poolPage >= pages:
            self._poolPage = pages - 1
        start = self._poolPage * PAGE_SIZE
        lines = []
        for iid, cn in entries[start:start + PAGE_SIZE]:
            lines.append("%s  %s" % (iid, cn) if cn else iid)
        if not lines:
            lines.append("（无匹配物品）" if self._pool else "（奖池加载中…）")
        self._SetLabel("/pool_dialog/title_label",
                       "奖池：共 %d 个物品（匹配 %d）" % (total, matched))
        self._SetLabel("/pool_dialog/list_label", "\n".join(lines))
        self._SetLabel("/pool_dialog/page_label",
                       "%d/%d" % (self._poolPage + 1, pages))

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
        self.RefreshPool()

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
