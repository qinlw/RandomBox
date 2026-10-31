# -*- coding: utf-8 -*-
"""RandomBox 服务端系统

随机箱子 = 随机口袋 + 原版箱子：
    1. 方块形态，必须放置到世界中，右键才能打开（不能随身携带打开）
    2. 容量 27 格，与原版箱子一致
    3. 每过 1 个游戏日，每个箱子自动刷新 1 个随机物品（箱子独立计数，满了当天不刷新）
    4. 放置时自动补填 1 个初始物品，避免第一天是空箱子
    5. 破坏时箱内物品跟着箱子物品走：内容转存到掉落物的 extraId 上，
       重新放回世界时原样还原（相当于随身收起的随机箱）
    6. 首次进服赠送 1 个随机箱子
    7. 支持 /rp test 测试模式（奖池查看 / 立即刷新一天）

放置后的箱子以“方块坐标”作为身份标识，内容保存在世界 ExtraData 中：
    rbx_all_chests = {"chests": {"dim:x:y:z": {"dim":..,"x":..,"y":..,"z":..,
                                              "day": int, "bag": {"槽位": 物品}, "seeded": bool}},
                      "boxes": {extraId: {"bag": {...}, "day": int, "t": 时间戳}}}
箱子被挖掉时坐标数据转成 boxes 里的 extraId 记录，并写进掉落物；
重新放置时再按 extraId 取回，多人共享同一箱子也不会串数据。
"""

import json
import random
import time

import mod.server.extraServerApi as serverApi

import randomboxScripts.loot_pool as loot_pool

ServerSystem = serverApi.GetServerSystemCls()
CompFactory = serverApi.GetEngineCompFactory()

try:
    _STRING_TYPES = (str, unicode)
except NameError:
    _STRING_TYPES = (str,)

# ============ 可配置项 ============
BLOCK_ID = "randombox:random_box"
BLOCK_FULL_ID = "randombox:random_box_full"
BLOCK_IDS = (BLOCK_ID, BLOCK_FULL_ID)

ITEMS_PER_DAY = 1
MAX_SLOTS = 27
DAY_TICKS = 24000
# 容器界面持续打开超过这个秒数就不再阻止满/未满外观切换
OPEN_STATE_TIMEOUT = 20.0
GRANT_KEY = "randombox_granted"
WORLD_KEY = "rbx_all_chests"

CONTAINER_SCREEN = "randombox_container.randombox_container_screen"
CONTAINER_TITLE = "随机箱子"
UI_COLLECTION_TYPE = "netease_ui_container"

MOD_NAMESPACE = "randombox"
CLIENT_SYSTEM = "RandomBoxClientSystem"
SERVER_SYSTEM = "RandomBoxServerSystem"
SCREEN_EVENT = "rbx_screen_event"
# 开箱/关箱时通知所有客户端播放箱盖动画
BOX_ANIM_EVENT = "rbx_box_anim"

# 测试模式（/rp test 或 /rb test 开启；会话内有效，退出后重置）
TEST_STATE_EVENT = "rbx_test_state"
TEST_POOL_CHUNK_EVENT = "rbx_test_pool_chunk"
TEST_POOL_REQUEST_EVENT = "rbx_test_pool_request"
TEST_REFRESH_EVENT = "rbx_test_refresh"
TEST_POOL_CHUNK_SIZE = 150
# ==================================

_levelId = None


def _LevelId():
    global _levelId
    if _levelId is None:
        _levelId = serverApi.GetLevelId()
    return _levelId


def _ItemName(it):
    """兼容 newItemName / itemName 两种物品字典键"""
    if not isinstance(it, dict):
        return ""
    return it.get("newItemName") or it.get("itemName") or ""


class RandomBoxServerSystem(ServerSystem):
    def __init__(self, namespace, systemName):
        ServerSystem.__init__(self, namespace, systemName)
        self._warned = set()
        self._booted = set()
        self._lastDay = None
        self._lastScan = 0.0
        self._prevTicks = None
        self._wrapBonus = 0
        self._rtBase = None
        self._rtBaseDay = 0
        self._openChest = {}      # playerId -> 当前打开的箱子 key
        self._openTime = {}       # playerId -> 打开界面的时间
        self._syncPending = set()
        self._useGuard = {}
        self._timerId = None
        self._pool = []            # 运行时物品奖池
        self._stackCache = {}      # 物品最大堆叠数缓存
        self._nameCache = {}       # 物品本地化名称缓存（测试面板用）
        self._testMode = set()     # 处于测试模式的玩家
        self._chests = {}          # 世界中的箱子数据（key -> state）
        self._boxes = {}           # 被挖走、尚未重新放置的箱子内容（extraId -> 记录）
        self._pendingPlace = {}    # playerId -> 即将放置的箱子物品 extraId
        self._statePending = set() # 需要核对满/未满外观的箱子 key
        self._dirty = False

        # 任何一步失败都不能影响系统注册，否则所有事件和定时器都不会生效
        try:
            self._LoadWorld()
        except Exception as e:
            print("[RandomBox] world load error:", e)
        try:
            self._ListenEngineEvents()
        except Exception as e:
            print("[RandomBox] listen error:", e)

        # 定时器：优先 Game 组件，失败再退回 ServerSystem 自带接口
        try:
            self._timerId = CompFactory.CreateGame(_LevelId()).AddRepeatedTimer(
                1.0, self.OnSecondTimer)
            print("[RandomBox] timer(started) game comp")
        except Exception as e:
            print("[RandomBox] game comp timer error:", e)
            try:
                self._timerId = self.AddRepeatedTimer(1.0, self.OnSecondTimer)
                print("[RandomBox] timer(started) system")
            except Exception as e2:
                print("[RandomBox] system timer error:", e2)
        try:
            self._EnsurePool()
        except Exception as e:
            print("[RandomBox] pool init error:", e)
        print("[RandomBox] server system started, chests=%d" % len(self._chests))

    def _ListenEngineEvents(self):
        engNs = serverApi.GetEngineNamespace()
        engSys = serverApi.GetEngineSystemName()
        for evtName in ("ServerItemTryUseEvent", "ServerItemUseOnEvent",
                        "ServerChatEvent", "CommandEvent",
                        "ServerBlockUseEvent",
                        "EntityPlaceBlockAfterServerEvent",
                        "DestroyBlockEvent", "BlockRemoveServerEvent",
                        "PlayerTryAddCustomContainerItemServerEvent",
                        "PlayerTryPutCustomContainerItemServerEvent",
                        "PlayerTryRemoveCustomContainerItemServerEvent"):
            handler = getattr(self, evtName, None)
            if handler is not None:
                try:
                    self.ListenForEvent(engNs, engSys, evtName, self, handler)
                except Exception as e:
                    print("[RandomBox] listen %s error: %s" % (evtName, e))
        # 客户端转发过来的界面打开/关闭事件
        try:
            self.ListenForEvent(MOD_NAMESPACE, CLIENT_SYSTEM, SCREEN_EVENT,
                                self, self.OnScreenEvent)
        except Exception as e:
            print("[RandomBox] listen screen event error: %s" % e)
        # 测试面板请求
        for evtName, handler in ((TEST_POOL_REQUEST_EVENT, self.OnTestPoolRequest),
                                 (TEST_REFRESH_EVENT, self.OnTestRefresh)):
            try:
                self.ListenForEvent(MOD_NAMESPACE, CLIENT_SYSTEM, evtName,
                                    self, handler)
            except Exception as e:
                print("[RandomBox] listen %s error: %s" % (evtName, e))

    def Destroy(self):
        try:
            if self._timerId:
                CompFactory.CreateGame(_LevelId()).CancelTimer(self._timerId)
        except Exception:
            pass
        try:
            ServerSystem.Destroy(self)
        except Exception:
            pass

    # ---------- 世界时间 ----------
    def _WorldClock(self):
        """返回 (day, ticks)；取不到返回 (None, None)"""
        try:
            raw = CompFactory.CreateTime(_LevelId()).GetTime()
        except Exception as e:
            print("[RandomBox] GetTime error:", e)
            return None, None
        if isinstance(raw, (tuple, list)):
            vals = []
            for v in raw:
                try:
                    vals.append(int(v))
                except Exception:
                    vals.append(0)
            if not vals:
                return None, None
            if len(vals) == 1:
                ticks = vals[0]
                return ticks // DAY_TICKS, ticks
            day, tod = vals[0], vals[1]
            if tod < 0 or tod >= DAY_TICKS:
                ticks = max(day, tod)
                return ticks // DAY_TICKS, ticks
            return day, day * DAY_TICKS + tod
        try:
            ticks = int(raw)
        except Exception as e:
            print("[RandomBox] GetTime invalid:", e, repr(raw))
            return None, None
        return ticks // DAY_TICKS, ticks

    def _ClockDay(self):
        """综合世界时间与真实时间算出当前游戏日；完全取不到时返回 None"""
        now = time.time()
        day, ticks = self._WorldClock()
        if day is not None:
            if self._prevTicks is not None and ticks < self._prevTicks - DAY_TICKS // 6:
                self._wrapBonus += 1
            self._prevTicks = ticks
            day += self._wrapBonus
        if self._rtBase is None and day is not None:
            self._rtBase = now
            self._rtBaseDay = day
        if self._rtBase is not None:
            # 兜底：即使世界时间读不到或停住，每 20 分钟真实时间也推进一天
            rtDay = self._rtBaseDay + int((now - self._rtBase) / 1200.0)
            day = rtDay if day is None else max(day, rtDay)
        return day

    # ---------- 世界箱子数据 ----------
    def _LoadWorld(self):
        raw = None
        try:
            raw = CompFactory.CreateExtraData(_LevelId()).GetExtraData(WORLD_KEY)
        except Exception as e:
            print("[RandomBox] world GetExtraData error:", e)
            return
        if isinstance(raw, _STRING_TYPES) and raw:
            try:
                data = json.loads(raw)
            except Exception as e:
                print("[RandomBox] world json error:", e)
                return
            if isinstance(data, dict):
                chests = data.get("chests")
                if isinstance(chests, dict):
                    self._chests = chests
                boxes = data.get("boxes")
                if isinstance(boxes, dict):
                    self._boxes = boxes
        self._PruneBoxes()
        # 存档里已有的箱子先排队核对一次外观（上次可能没来得及切满箱贴图）
        for key in list(self._chests.keys()):
            self._statePending.add(key)
        print("[RandomBox] world loaded chests=%d boxes=%d"
              % (len(self._chests), len(self._boxes)))

    def _SaveWorld(self):
        try:
            ok = CompFactory.CreateExtraData(_LevelId()).SetExtraData(
                WORLD_KEY, json.dumps({"chests": self._chests,
                                       "boxes": self._boxes}))
            if not ok:
                print("[RandomBox] world SetExtraData returned False")
        except Exception as e:
            print("[RandomBox] world SetExtraData error:", e)
        self._dirty = False

    def _PruneBoxes(self):
        """清理长时间没被重新放置的箱子记录，避免 ExtraData 无限增长"""
        limit = time.time() - 30 * 24 * 3600
        stale = [bid for bid, rec in self._boxes.items()
                 if not isinstance(rec, dict) or rec.get("t", 0) < limit]
        for bid in stale:
            del self._boxes[bid]
        if stale:
            print("[RandomBox] prune stale boxes=%d" % len(stale))

    @staticmethod
    def _Key(dim, x, y, z):
        return "%d:%d:%d:%d" % (int(dim), int(x), int(y), int(z))

    def _GetChest(self, dim, x, y, z):
        key = self._Key(dim, x, y, z)
        state = self._chests.get(key)
        if isinstance(state, dict):
            state["key"] = key
            return key, state
        state = {
            "key": key,
            "dim": int(dim),
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "day": None,
            "bag": {},
            "seeded": False,
        }
        self._chests[key] = state
        self._dirty = True
        return key, state

    def _DropChest(self, dim, x, y, z, playerId=None):
        """挖掉箱子：把箱内物品绑到掉落物的 extraId 上，重新放置时原样还原"""
        key = self._Key(dim, x, y, z)
        state = self._chests.get(key)
        if not isinstance(state, dict):
            return
        boxId = self._NewBoxId()
        self._boxes[boxId] = {
            "bag": self._Bag(state),
            "day": state.get("day"),
            "t": time.time(),
        }
        del self._chests[key]
        self._statePending.discard(key)
        if not self._SpawnBoxItem(dim, x, y, z, boxId, playerId):
            # 掉落失败时把数据放回去，避免物品凭空消失
            self._chests[key] = state
            self._boxes.pop(boxId, None)
            print("[RandomBox] spawn box item failed, keep chest %s" % key)
            return
        self._dirty = True
        self._SaveWorld()
        print("[RandomBox] chest %s -> item %s (%d items)"
              % (key, boxId, len(self._Bag(state))))

    def _NewBoxId(self):
        while True:
            boxId = "rbx%08x%04x" % (int(time.time()) & 0xffffffff,
                                    random.randint(0, 0xffff))
            if boxId not in self._boxes:
                return boxId

    def _SpawnBoxItem(self, dim, x, y, z, boxId, playerId=None):
        """生成带 extraId 的随机箱子物品：能进背包就进背包，否则掉在地上"""
        itemDict = {"newItemName": BLOCK_ID, "newAuxValue": 0,
                    "count": 1, "extraId": boxId}
        if playerId:
            try:
                if CompFactory.CreateItem(playerId).SpawnItemToPlayerInv(
                        itemDict, playerId):
                    return True
            except Exception as e:
                print("[RandomBox] SpawnItemToPlayerInv error:", e)
        comp = CompFactory.CreateItem(_LevelId())
        try:
            comp.SpawnItemToLevel(itemDict, int(dim),
                                  (int(x), int(y), int(z)))
            return True
        except Exception as e:
            print("[RandomBox] SpawnItemToLevel(dim,pos) error:", e)
        try:
            comp.SpawnItemToLevel(itemDict, (int(x), int(y), int(z)), int(dim))
            return True
        except Exception as e:
            print("[RandomBox] SpawnItemToLevel(pos,dim) error:", e)
        return False

    def _BindBoxItem(self, key, state, boxId):
        """把物品上的箱子内容绑定到新放置的坐标"""
        rec = self._boxes.pop(boxId, None)
        if not isinstance(rec, dict):
            return False
        bag = rec.get("bag")
        if not isinstance(bag, dict):
            bag = {}
        state["bag"] = bag
        state["day"] = self._ClockDay() or state.get("day") or 0
        state["seeded"] = True
        self._dirty = True
        self._SaveWorld()
        self._MarkState(key)
        return True

    def _TakePendingBox(self, playerId):
        """取出刚刚用来放置的箱子物品标识（3 秒内有效，防止误用旧记录）"""
        now = time.time()
        for pid in list(self._pendingPlace.keys()):
            rec = self._pendingPlace[pid]
            if now - (rec.get("t") or 0) > 3.0:
                del self._pendingPlace[pid]
        if playerId and playerId in self._pendingPlace:
            rec = self._pendingPlace.pop(playerId)
            return rec.get("boxId")
        if len(self._pendingPlace) == 1:
            rec = self._pendingPlace.pop(list(self._pendingPlace.keys())[0])
            return rec.get("boxId")
        return None

    # ---------- 每秒轮询 ----------
    def OnSecondTimer(self, *args):
        try:
            players = serverApi.GetPlayerList()
            # 玩家退出后清理残留的打开记录，避免永远挡住外观切换
            for pid in list(self._openChest.keys()):
                if pid not in players:
                    key = self._openChest.pop(pid, None)
                    self._openTime.pop(pid, None)
                    self._syncPending.discard(pid)
                    self._AnimClosed(key)
                    self._MarkState(key)
            for playerId in players:
                try:
                    self._GrantBox(playerId)
                except Exception as e:
                    print("[RandomBox] grant error:", e)
            # 每秒核对一次排队中的箱子外观，覆盖界面关闭事件漏发等情况
            self._SweepStates()
            day = self._ClockDay()
            if day is None:
                return
            now = time.time()
            if day == self._lastDay and now - self._lastScan < 2.0:
                return
            self._lastDay = day
            self._lastScan = now
            self._RefreshAll(day)
        except Exception as e:
            print("[RandomBox] timer error:", e)

    # ---------- 物品格式 ----------
    @staticmethod
    def _CleanItem(it):
        return {
            "newItemName": _ItemName(it),
            "newAuxValue": it.get("newAuxValue", it.get("auxValue", 0)),
            "count": it.get("count", 1),
        }

    @staticmethod
    def _NormalizeItem(it):
        out = {
            "newItemName": _ItemName(it),
            "newAuxValue": it.get("newAuxValue", it.get("auxValue", 0)),
            "count": it.get("count", 1),
        }
        eid = it.get("extraId")
        if isinstance(eid, _STRING_TYPES):
            out["extraId"] = eid
        return out

    @staticmethod
    def _Bag(state):
        """箱子内容：dict，键为槽位号字符串，值为物品字典"""
        bag = state.get("bag")
        if isinstance(bag, dict):
            return bag
        if isinstance(bag, list):
            out = {}
            for i, it in enumerate(bag):
                if isinstance(it, dict) and _ItemName(it):
                    out[str(i)] = it
            return out
        return {}

    @staticmethod
    def _BagAdd(bag, item, size=MAX_SLOTS):
        """把物品放入第一个空槽位；满则返回 False"""
        for slot in range(size):
            if str(slot) not in bag:
                bag[str(slot)] = item
                return True
        return False

    @staticmethod
    def _HandledToday(state, day):
        rbDay = state.get("day")
        if rbDay is None:
            return False
        try:
            return day <= int(rbDay)
        except Exception:
            return False

    # ---------- 奖池 ----------
    def _EnsurePool(self):
        """运行时读取当前游戏与已加载模组的物品，过滤后缓存为奖池"""
        if self._pool:
            return self._pool
        comp = CompFactory.CreateItem(_LevelId())
        for flag, label in ((False, "creative"), (True, "all")):
            try:
                ids = comp.GetLoadItems(flag)
            except Exception as e:
                print("[RandomBox] GetLoadItems(%s) error: %s" % (flag, e))
                continue
            pool = loot_pool.BuildPool(ids)
            if pool:
                self._pool = sorted(pool)
                print("[RandomBox] pool %s items=%d" % (label, len(pool)))
                return self._pool
        self._pool = list(loot_pool.POOL)
        print("[RandomBox] pool fallback items=%d" % len(self._pool))
        return self._pool

    def _MaxStack(self, name):
        if name in self._stackCache:
            return self._stackCache[name]
        limit = 0
        try:
            info = CompFactory.CreateItem(_LevelId()).GetItemBasicInfo(name, 0)
            if isinstance(info, dict):
                limit = int(info.get("maxStackSize") or 0)
        except Exception as e:
            print("[RandomBox] GetItemBasicInfo error:", e)
        self._stackCache[name] = limit
        return limit

    def _RollItem(self):
        return loot_pool.RollItem(self._EnsurePool(), self._MaxStack)

    # ---------- 测试模式 ----------
    def ServerChatEvent(self, args):
        """聊天指令：/rp test、#rp test（同时兼容 /rb）"""
        try:
            if not isinstance(args, dict):
                return
            if not self._HandleTestCommand(args.get("playerId"),
                                           args.get("message")):
                return
            args["cancel"] = True
        except Exception as e:
            print("[RandomBox] chat event error:", e)

    def CommandEvent(self, args):
        """指令 Hook：/rp test"""
        try:
            if not isinstance(args, dict):
                return
            playerId = args.get("entityId") or args.get("playerId")
            raw = args.get("command")
            if isinstance(raw, _STRING_TYPES) and raw[:1] not in ("/", "#", "!", "！"):
                raw = "/" + raw
            if not self._HandleTestCommand(playerId, raw):
                return
            args["cancel"] = True
        except Exception as e:
            print("[RandomBox] command event error:", e)

    def _HandleTestCommand(self, playerId, raw):
        if not playerId or not isinstance(raw, _STRING_TYPES):
            return False
        text = raw.strip()
        if not text or text[0] not in ("/", "#", "!", "！"):
            return False
        words = text[1:].split()
        if not words or words[0].lower() not in ("rp", "rb"):
            return False
        sub = [w.lower() for w in words[1:]]
        if sub and sub[0] != "test":
            return False
        want = None
        if len(sub) >= 2:
            if sub[1] in ("on", "true", "1", "开"):
                want = True
            elif sub[1] in ("off", "false", "0", "关"):
                want = False
        self._SetTestMode(playerId, want)
        return True

    def _SetTestMode(self, playerId, want=None):
        enabled = (playerId not in self._testMode) if want is None else bool(want)
        if enabled:
            self._testMode.add(playerId)
            self._Notify(playerId, "[随机箱子] 测试模式已开启：打开箱子后可查看奖池 / 刷新一天")
            self._SendTestState(playerId, True)
            self._SendPoolChunks(playerId)
        else:
            self._testMode.discard(playerId)
            self._Notify(playerId, "[随机箱子] 测试模式已关闭")
            self._SendTestState(playerId, False)
        print("[RandomBox] test mode %s for %s" % (enabled, playerId))

    def _SendTestState(self, playerId, enabled):
        try:
            self.NotifyToClient(playerId, TEST_STATE_EVENT,
                                {"enabled": bool(enabled)})
        except Exception as e:
            print("[RandomBox] notify test state error:", e)

    def _PoolEntry(self, name):
        cn = self._nameCache.get(name)
        if cn is None:
            cn = ""
            try:
                info = CompFactory.CreateItem(_LevelId()).GetItemBasicInfo(name, 0)
                if isinstance(info, dict):
                    cn = info.get("itemName") or ""
            except Exception:
                pass
            self._nameCache[name] = cn
        return {"id": name, "name": cn}

    def _SendPoolChunks(self, playerId):
        pool = self._EnsurePool()
        size = TEST_POOL_CHUNK_SIZE
        total = max(1, (len(pool) + size - 1) // size)
        for index in range(total):
            try:
                part = pool[index * size:(index + 1) * size]
                self.NotifyToClient(playerId, TEST_POOL_CHUNK_EVENT, {
                    "index": index,
                    "total": total,
                    "items": [self._PoolEntry(n) for n in part],
                })
            except Exception as e:
                print("[RandomBox] send pool chunk error:", e)
                return
        print("[RandomBox] send pool to %s: %d items / %d chunks"
              % (playerId, len(pool), total))

    def OnTestPoolRequest(self, args):
        try:
            playerId = args.get("playerId")
            if playerId and playerId in self._testMode:
                self._SendPoolChunks(playerId)
        except Exception as e:
            print("[RandomBox] test pool request error:", e)

    def OnTestRefresh(self, args):
        try:
            playerId = args.get("playerId")
            if playerId and playerId in self._testMode:
                self._TestRefresh(playerId)
        except Exception as e:
            print("[RandomBox] test refresh error:", e)

    def _TestRefresh(self, playerId):
        """立即模拟一次每日刷新：给当前打开的箱子加 1 件物品"""
        key = self._openChest.get(playerId)
        if not key or key not in self._chests:
            self._Notify(playerId, "[随机箱子] 测试：请先打开一个随机箱子")
            return
        state = self._chests[key]
        bag = self._Bag(state)
        if len(bag) >= MAX_SLOTS:
            self._Notify(playerId, "[随机箱子] 测试：箱子已满")
            return
        self._BagAdd(bag, self._RollItem())
        state["bag"] = bag
        self._dirty = True
        self._SaveWorld()
        self._Notify(playerId, "[随机箱子] 测试刷新 +1 (%d/%d)" %
                     (len(bag), MAX_SLOTS))
        self._PushBagToUI(playerId, bag)
        self._MarkState(key)

    # ---------- 放置 / 破坏 ----------
    def ServerItemUseOnEvent(self, args):
        """放置随机箱子前，记录该物品携带的箱子内容标识"""
        try:
            if not isinstance(args, dict):
                return
            it = args.get("itemDict") or {}
            if _ItemName(it) != BLOCK_ID:
                return
            boxId = it.get("extraId")
            if not isinstance(boxId, _STRING_TYPES) or not boxId:
                return
            playerId = args.get("entityId") or args.get("playerId")
            if not playerId:
                return
            self._pendingPlace[playerId] = {"boxId": boxId, "t": time.time()}
            print("[RandomBox] pending box item %s for %s" % (boxId, playerId))
        except Exception as e:
            print("[RandomBox] item use on error:", e)

    def EntityPlaceBlockAfterServerEvent(self, args):
        """玩家放下随机箱子：登记坐标并预填 1 个初始物品；带内容的物品原样还原"""
        try:
            if not isinstance(args, dict):
                return
            name = args.get("fullName") or args.get("blockName") or ""
            if name != BLOCK_ID:
                return
            dim = args.get("dimensionId", 0)
            x, y, z = args.get("x"), args.get("y"), args.get("z")
            if None in (x, y, z):
                return
            playerId = args.get("playerId") or args.get("entityId")
            key, state = self._EnsureChest(dim, x, y, z, seed=True)
            boxId = self._TakePendingBox(playerId)
            if boxId and self._BindBoxItem(key, state, boxId):
                print("[RandomBox] restore chest %s from item %s"
                      % (key, boxId))
            print("[RandomBox] place chest at %s" % key)
        except Exception as e:
            print("[RandomBox] place event error:", e)

    def DestroyBlockEvent(self, args):
        self._OnBlockGone(args)

    def BlockRemoveServerEvent(self, args):
        self._OnBlockGone(args)

    def _OnBlockGone(self, args):
        try:
            if not isinstance(args, dict):
                return
            name = args.get("blockName") or args.get("fullName") or ""
            if name not in BLOCK_IDS:
                return
            dim = args.get("dimensionId", 0)
            x, y, z = args.get("x"), args.get("y"), args.get("z")
            if None in (x, y, z):
                return
            # 满/未满状态切换同样会触发移除事件，此时同一位置仍是随机箱子，
            # 不能把数据取走（真正被挖掉时该位置已变成空气）
            cur = self._BlockName(dim, x, y, z)
            if cur in BLOCK_IDS:
                # 破坏事件有可能早于方块真正消失，稍后再确认一次，
                # 避免箱子挖掉后物品没生成导致内容丢失
                try:
                    CompFactory.CreateGame(_LevelId()).AddTimer(
                        0.2, self._RecheckGone, dim, x, y, z)
                except Exception as e:
                    print("[RandomBox] recheck timer error:", e)
                return
            playerId = args.get("playerId") or args.get("entityId")
            self._DropChest(dim, x, y, z, playerId)
        except Exception as e:
            print("[RandomBox] block gone error:", e)

    def _RecheckGone(self, dim, x, y, z):
        try:
            if self._BlockName(dim, x, y, z) in BLOCK_IDS:
                return
            self._DropChest(dim, x, y, z)
        except Exception as e:
            print("[RandomBox] recheck gone error:", e)

    # ---------- 满箱外观切换 ----------
    def _BlockComp(self, method):
        """取带指定方法的方块组件：新接口在 blockInfo 组件上，旧接口在 block 组件上"""
        for creator in ("CreateBlockInfo", "CreateBlock"):
            fn = getattr(CompFactory, creator, None)
            if fn is None:
                continue
            try:
                comp = fn(_LevelId())
            except Exception:
                continue
            if comp is not None and hasattr(comp, method):
                return comp
        return None

    def _BlockData(self, dim, x, y, z):
        """读取坐标处方块 (name, aux)；读不到（未加载等）返回 (None, None)"""
        comp = self._BlockComp("GetBlockNew")
        if comp is None:
            return None, None
        try:
            info = comp.GetBlockNew((int(x), int(y), int(z)), int(dim))
        except Exception as e:
            print("[RandomBox] GetBlockNew error:", e)
            return None, None
        if isinstance(info, dict):
            name = info.get("name")
            aux = info.get("aux", info.get("auxValue", info.get("auxData", 0)))
            try:
                aux = int(aux)
            except Exception:
                aux = 0
            return name, aux
        if isinstance(info, _STRING_TYPES):
            return info, 0
        return None, None

    def _BlockName(self, dim, x, y, z):
        """读取坐标处方块 ID；读不到（未加载等）返回 None"""
        return self._BlockData(dim, x, y, z)[0]

    def _IsChestOpen(self, key):
        for playerId, openKey in self._openChest.items():
            if openKey != key:
                continue
            # 界面开了很久多半是关闭事件没上报，不再阻塞外观切换
            if time.time() - self._openTime.get(playerId, 0) > OPEN_STATE_TIMEOUT:
                continue
            return True
        return False

    def _BroadcastAnim(self, key, opening):
        """广播箱盖开/合，让所有客户端播放对应动画"""
        state = self._chests.get(key)
        if not isinstance(state, dict):
            return
        x, y, z = state.get("x"), state.get("y"), state.get("z")
        if None in (x, y, z):
            return
        try:
            self.BroadcastToAllClient(BOX_ANIM_EVENT, {
                "x": int(x), "y": int(y), "z": int(z),
                "dim": int(state.get("dim", 0)),
                "open": bool(opening),
            })
        except Exception as e:
            print("[RandomBox] broadcast anim error:", e)

    def _AnimClosed(self, key):
        """没人在看这个箱子时才合上盖子"""
        if key and key in self._openChest.values():
            return
        self._BroadcastAnim(key, False)

    def _MarkState(self, key):
        """登记一个需要核对外观的箱子，由每秒 sweep 应用（界面关闭后才切换）"""
        if key:
            self._statePending.add(key)

    def _SweepStates(self):
        if not self._statePending:
            return
        for key in list(self._statePending)[:64]:
            try:
                if self._SetBlockState(key):
                    self._statePending.discard(key)
            except Exception as e:
                self._statePending.discard(key)
                print("[RandomBox] sweep state error:", e)

    def _SetBlockState(self, key):
        """箱子满 27 格时换成“已满”外观，腾出空位后恢复普通外观。
        返回 True 表示本轮已处理完；False 表示暂时不能切换（箱子正在被打开）。"""
        state = self._chests.get(key)
        if not isinstance(state, dict):
            return True
        # 有人正在使用这个箱子时不切换，避免容器界面被强制关闭
        if self._IsChestOpen(key):
            return False
        bag = self._Bag(state)
        want = BLOCK_FULL_ID if len(bag) >= MAX_SLOTS else BLOCK_ID
        dim = state.get("dim", 0)
        x, y, z = state.get("x"), state.get("y"), state.get("z")
        if None in (x, y, z):
            return True
        cur, aux = self._BlockData(dim, x, y, z)
        if cur is None:
            # 区块未加载，稍后再试
            return False
        if cur not in BLOCK_IDS:
            # 方块已经不在了（被替换成别的方块），数据交给破坏事件处理
            return True
        if cur == want:
            return True
        comp = self._BlockComp("SetBlockNew")
        if comp is None:
            print("[RandomBox] no block component with SetBlockNew")
            return True
        pos = (int(x), int(y), int(z))
        # 保留原来的 aux，否则四面向（箱子朝向）会被重置
        blockDict = {"name": want, "aux": aux}
        ok = False
        # oldBlockHandling: 0=替换 1=销毁 2=保留；第四个参数是维度
        try:
            ok = comp.SetBlockNew(pos, blockDict, 0, int(dim))
        except Exception as e:
            print("[RandomBox] SetBlockNew(dim) error:", e)
        if not ok:
            try:
                ok = comp.SetBlockNew(pos, blockDict, 0)
            except Exception as e:
                print("[RandomBox] SetBlockNew error:", e)
        print("[RandomBox] block state %s -> %s ok=%s" % (cur, want, ok))
        return True

    def _EnsureChest(self, dim, x, y, z, seed=False):
        """取箱子数据；首次登记时可选立即补 1 个初始物品"""
        day = self._ClockDay()
        if day is None:
            day = 0
        key, state = self._GetChest(dim, x, y, z)
        changed = False
        if state.get("day") is None:
            state["day"] = day
            changed = True
        bag = self._Bag(state)
        if seed and not bag and not state.get("seeded"):
            self._BagAdd(bag, self._RollItem())
            state["seeded"] = True
            state["bag"] = bag
            changed = True
        if changed:
            self._dirty = True
            self._SaveWorld()
            self._MarkState(key)
        return key, state

    # ---------- 右键箱子 -> 打开容器 ----------
    def ServerBlockUseEvent(self, args):
        try:
            if not isinstance(args, dict):
                return
            if args.get("blockName") not in BLOCK_IDS:
                return
            playerId = args.get("playerId")
            if not playerId:
                return
            dim = args.get("dimensionId", 0)
            x, y, z = args.get("x"), args.get("y"), args.get("z")
            if None in (x, y, z):
                return

            now = time.time()
            if now - self._useGuard.get(playerId, 0) < 0.4:
                return
            self._useGuard[playerId] = now

            self._OpenChest(playerId, dim, x, y, z)
        except Exception as e:
            print("[RandomBox] block use error:", e)

    def _OpenChest(self, playerId, dim, x, y, z):
        key, state = self._EnsureChest(dim, x, y, z, seed=True)
        bag = self._Bag(state)
        state["bag"] = bag
        self._PushBagToUI(playerId, bag)
        # 换箱子前先合上上一个箱子的盖子
        prev = self._openChest.pop(playerId, None)
        if prev and prev != key:
            self._AnimClosed(prev)
        self._openChest[playerId] = key
        self._openTime[playerId] = time.time()

        try:
            CompFactory.CreatePlayer(playerId).OpenNeteaseContainer(
                CONTAINER_SCREEN, CONTAINER_TITLE)
            print("[RandomBox] open chest %s (%d items) for %s" %
                  (key, len(bag), playerId))
        except Exception as e:
            print("[RandomBox] OpenNeteaseContainer error:", e)
            self._Notify(playerId, "[随机箱子] 打开失败 (当前%d个物品)" % len(bag))
            return

        # 播放开盖动画
        self._BroadcastAnim(key, True)

        # 界面打开时引擎可能清空预先写入的槽位，打开后再补推一次
        try:
            CompFactory.CreateGame(_LevelId()).AddTimer(
                0.4, self._PushAfterOpen, playerId)
        except Exception as e:
            print("[RandomBox] push timer error:", e)

    def _PushAfterOpen(self, playerId):
        key = self._openChest.get(playerId)
        if not key:
            return
        state = self._chests.get(key)
        if not isinstance(state, dict):
            return
        bag = self._Bag(state)
        state["bag"] = bag
        self._PushBagToUI(playerId, bag)
        print("[RandomBox] push after open %s" % key)

    def OnScreenEvent(self, args):
        """客户端上报的界面打开/关闭"""
        try:
            action = args.get("action")
            playerId = args.get("playerId")
            if not playerId:
                return
            if action == "push":
                self._PushAfterOpen(playerId)
                print("[RandomBox] screen push for %s" % playerId)
            elif action == "pop":
                key = self._openChest.pop(playerId, None)
                self._openTime.pop(playerId, None)
                self._syncPending.discard(playerId)
                # 关闭界面后再切换满/未满外观，避免打断正在使用的容器
                if key:
                    self._AnimClosed(key)
                    self._MarkState(key)
                    self._SweepStates()
                print("[RandomBox] screen pop for %s" % playerId)
        except Exception as e:
            print("[RandomBox] screen event error:", e)

    # ---------- 容器界面数据维护 ----------
    def _PushBagToUI(self, playerId, bag):
        comp = CompFactory.CreateItem(playerId)
        for slot in range(MAX_SLOTS):
            it = None
            src = bag.get(str(slot)) if isinstance(bag, dict) else None
            if isinstance(src, dict) and _ItemName(src):
                it = self._CleanItem(src)
            try:
                comp.SetPlayerUIItem(playerId, slot, it, False, True)
            except Exception as e:
                print("[RandomBox] SetPlayerUIItem error:", e)
                return False
        return True

    def _ReadUIContainer(self, playerId):
        comp = CompFactory.CreateItem(playerId)
        bag = {}
        for slot in range(MAX_SLOTS):
            try:
                it = comp.GetPlayerUIItem(playerId, slot, True, True)
            except Exception as e:
                print("[RandomBox] GetPlayerUIItem error:", e)
                return None
            if isinstance(it, dict) and _ItemName(it):
                try:
                    count = int(it.get("count", 1))
                except Exception:
                    count = 1
                if count > 0:
                    bag[str(slot)] = self._CleanItem(it)
        return bag

    def _SyncOpenContainer(self, playerId):
        key = self._openChest.get(playerId)
        if not key:
            return
        bag = self._ReadUIContainer(playerId)
        if bag is None:
            return
        state = self._chests.get(key)
        if not isinstance(state, dict):
            return
        state["bag"] = bag
        self._dirty = True
        self._SaveWorld()
        # 玩家可能刚放完最后一件就关掉界面，关闭后由 sweep 切换到“已满”外观
        self._MarkState(key)

    def _RequestSync(self, playerId, delay=0.25):
        if playerId in self._syncPending:
            return
        self._syncPending.add(playerId)
        try:
            CompFactory.CreateGame(_LevelId()).AddTimer(delay, self._DoSync, playerId)
        except Exception as e:
            self._syncPending.discard(playerId)
            print("[RandomBox] AddTimer error:", e)

    def _DoSync(self, playerId):
        self._syncPending.discard(playerId)
        try:
            self._SyncOpenContainer(playerId)
        except Exception as e:
            print("[RandomBox] sync error:", e)

    def _OnContainerChanged(self, args):
        try:
            ctype = args.get("collectionType")
            if ctype and ctype != UI_COLLECTION_TYPE:
                return
            playerId = args.get("playerId")
            if not playerId:
                return
            if not self._openChest.get(playerId):
                return
            self._RequestSync(playerId)
        except Exception as e:
            print("[RandomBox] container event error:", e)

    def PlayerTryAddCustomContainerItemServerEvent(self, args):
        self._OnContainerChanged(args)

    def PlayerTryPutCustomContainerItemServerEvent(self, args):
        self._OnContainerChanged(args)

    def PlayerTryRemoveCustomContainerItemServerEvent(self, args):
        self._OnContainerChanged(args)

    # ---------- 每日刷新 ----------
    def _RefreshAll(self, day):
        """每个箱子每天独立刷新 1 个物品；满了当天不刷新"""
        count = 0
        touched = []
        for key, state in self._chests.items():
            try:
                if not isinstance(state, dict):
                    continue
                if self._HandledToday(state, day):
                    continue
                state["day"] = day
                bag = self._Bag(state)
                got = 0
                while len(bag) < MAX_SLOTS and got < ITEMS_PER_DAY:
                    self._BagAdd(bag, self._RollItem())
                    got += 1
                state["bag"] = bag
                self._dirty = True
                if got > 0:
                    count += 1
                    touched.append(key)
                    self._PushToOpenPlayers(key, bag)
            except Exception as e:
                print("[RandomBox] refresh chest error:", e)
        if self._dirty:
            self._SaveWorld()
        for key in touched:
            self._MarkState(key)
        self._SweepStates()
        if count:
            print("[RandomBox] day=%d refreshed %d chests" % (day, count))
        return count

    def _PushToOpenPlayers(self, key, bag):
        for playerId, openKey in self._openChest.items():
            if openKey != key:
                continue
            try:
                self._PushBagToUI(playerId, bag)
                self._Notify(playerId, "[随机箱子] +1个物品 (%d/%d)" %
                             (len(bag), MAX_SLOTS))
            except Exception as e:
                print("[RandomBox] push open player error:", e)

    # ---------- 首次进服赠送一个随机箱子 ----------
    def _GrantBox(self, playerId):
        try:
            ed = CompFactory.CreateExtraData(playerId)
            if ed.GetExtraData(GRANT_KEY):
                return
        except Exception as e:
            print("[RandomBox] extradata error:", e)
            return
        if self._HasBoxItem(playerId):
            try:
                CompFactory.CreateExtraData(playerId).SetExtraData(GRANT_KEY, True)
            except Exception:
                pass
            return
        try:
            ok = CompFactory.CreateItem(playerId).SpawnItemToPlayerInv(
                {"newItemName": BLOCK_ID, "newAuxValue": 0, "count": 1}, playerId)
            print("[RandomBox] grant random box to %s -> %s" % (playerId, ok))
            if ok:
                CompFactory.CreateExtraData(playerId).SetExtraData(GRANT_KEY, True)
                self._Notify(playerId, "[随机箱子] 获得一个随机箱子")
        except Exception as e:
            print("[RandomBox] grant error:", e)

    def _HasBoxItem(self, playerId):
        itemComp = CompFactory.CreateItem(playerId)
        enum = serverApi.GetMinecraftEnum()
        posType = getattr(enum, "ItemPosType", None)
        if posType is None:
            return False
        ranges = (("CARRIED", range(0, 9)),
                  ("INVENTORY", range(0, 36)),
                  ("OFFHAND", range(0, 1)))
        for posName, slots in ranges:
            pt = getattr(posType, posName, None)
            if pt is None:
                continue
            for slot in slots:
                try:
                    it = itemComp.GetPlayerItem(pt, slot, True)
                except Exception:
                    continue
                if isinstance(it, dict) and _ItemName(it) == BLOCK_ID:
                    return True
        return False

    # ---------- 消息 ----------
    def _Notify(self, playerId, msg):
        print("[RandomBox] notify: %s" % msg)
        gameComp = CompFactory.CreateGame(_LevelId())
        try:
            gameComp.NotifyOneMessage(playerId, msg)
            return
        except Exception:
            pass
        try:
            gameComp.SetTipMessage(msg)
        except Exception:
            pass
