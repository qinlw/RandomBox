# RandomBox · 随机箱子

网易《我的世界》中国版（基岩版）Addon。在游戏里放一个会「自己长东西」的箱子：每个游戏日自动往箱子里塞一件随机物品，满了就不再增长，挖掉重新放置内容也不会丢。

---

## 一、玩法

### 1. 拿到箱子
- 首次进入世界时，系统会自动给玩家发 1 个随机箱子（每人只发一次，进背包并弹出提示）。
- 也可以从创造模式物品栏直接拿：`随机箱子`，或工作台**无序合成**：`箱子 + 绿宝石` → `随机箱子 ×1`。
- 箱子是普通方块，可放置、可挖取、可堆叠。

### 2. 自动刷新
- 场上**每一个**箱子都有独立的库存和计时。
- 每过一个**游戏日**（24000 ticks），每个箱子自动 +1 件随机物品。
- 箱子最多 27 格；**满了以后当天不再刷新**，外观会变成「满箱」状态（偏红铜色）。
- 界面开着的时候刷新，物品会实时出现在界面里。

### 3. 打开与外观
- 右键打开箱子，进入 27 格的容器界面（标题「随机箱子」）。
- 箱子有开盖动画：打开时盖子上翻，关闭时回落。
- 放置时按玩家朝向旋转（四面向），正面是**带锁扣的面板**，其余四面是带「?」的木板面，盖顶是木条纹理。
- 箱子内容与方块绑定，**不是**共用战利品表。

### 4. 挖掉再放置
- 挖掉箱子时，箱内所有物品会跟着这个箱子物品走（记录在物品的 `extraId` 上）。
- 重新放下同一个箱子物品，里面的东西原样还原，不会丢失。
- 拆除瞬间如果方块被其它方式替换，也会走同样的掉落逻辑。

---

## 二、奖池

奖池在运行时从引擎读取，**不用手工维护清单**：

1. 优先取「创造模式物品栏」全部物品；
2. 取不到时退化为「当前加载的全部物品」（会包含其它已加载模组的东西）；
3. 接口都失败时使用内置后备奖池 `loot_pool.py` 的 `POOL`。

生成时会被过滤掉：

- 技术方块 / 占位方块（空气、流水、屏障、结构方块、命令方块等）；
- 刷怪蛋、教育版与化学物品、旧版兼容 ID；
- 本模组自己的方块（避免「箱子开出箱子」）；
- 已废弃 / 双半砖 / 墙告示牌之类的技术条目。

每件物品数量为 1~3（不超过该物品的最大堆叠数，工具、盔甲、鞘翅、潜影盒等不可堆叠物品固定 1 件）。

---

## 三、测试指令

只需要测试时可临时打开测试面板：

```
/rp test            切换测试面板开关
/rp test on        打开
/rp test off       关闭
```

`/rb test`、`#rp test`、`!rp test` 写法也兼容。面板里可以查看当前奖池，「测试刷新」会给**当前打开的那个箱子**立刻 +1 件物品，方便验证满箱外观与开盖动画。

---

## 四、安装

**方式一（推荐）**：把 `RandomBox.mcaddon` 导入网易开发者工具 / 客户端即可。

**方式二（手动）**：

```
MinecraftPE_Netease/games/com.netease/
├── behavior_packs/behavior_pack/     ← behavior_pack 的内容
└── resource_packs/resource_pack/     ← resource_pack 的内容
```

需要注意行为包与资源包必须在同一个「存档」/「游戏」下加载，资源包负责贴图、模型与动画，缺一不可。

---

## 五、目录结构

```
RandomBox/
├── build.py                     打包脚本：生成 RandomBox.mcaddon / RandomBox.zip
├── behavior_pack/
│   ├── manifest.json
│   ├── netease_blocks/          随机箱子、满箱 的方块定义
│   ├── netease_recipes/         配方
│   ├── loot_tables/             空战利品表（内容由脚本维护）
│   ├── texts/                   语言文件（zh_CN.lang）
│   └── randomboxScripts/
│       ├── modMain.py           模组入口
│       ├── randomboxServer.py   服务端：库存、刷新、掉落、交互、动画广播
│       ├── randomboxClient.py   客户端：界面事件、实体动画控制
│       └── loot_pool.py         奖池构建与随机抽取
└── resource_pack/
    ├── manifest.json
    ├── blocks.json              方块图标 / 自定义方块实体绑定
    ├── models/entity/           箱子的 3D 模型（箱体 + 可转动的盖子）
    ├── animations/              开盖 / 合盖动画与「模型归位」动画
    ├── animation_controllers/   由 Molang 变量 rbx_open 驱动的状态机
    ├── entity/                  方块实体定义（普通箱 / 满箱）
    ├── render_controllers/
    ├── textures/
    │   ├── blocks/              方块面与物品图标、正面锁扣
    │   └── entity/randombox/    实体图集（48×16：侧面「?」/ 盖顶木条 / 正面锁扣）
    └── ui/                      容器界面
```

### 贴图对照

| 用途 | 文件 |
| --- | --- |
| 物品栏 / 手持图标（三面立体、正面锁扣） | `textures/blocks/random_box_icon.png`、`random_box_full_icon.png` |
| 方块四面（带「?」） | `textures/blocks/random_box_side.png`、`random_box_full_side.png` |
| 方块正面（锁扣） | `textures/blocks/random_box_front.png`、`random_box_full_front.png` |
| 盖顶（木条） | `textures/blocks/random_box_top.png`、`random_box_full_top.png` |
| 3D 实体取样图集 | `textures/entity/randombox/box_normal.png`、`box_full.png` |

> 放置后真正显示的是 3D 实体图集（最后一行）；`textures/blocks` 下的单面图只用于图标和编辑参考，两者需要同步修改。

---

## 六、打包

在项目根目录执行：

```bash
python build.py
```

会生成 `RandomBox.mcaddon` 与 `RandomBox.zip`，并做基本的目录结构校验（根目录只允许两个包、检查实体与贴图是否缺失）。

---

## 七、已知限制

- 箱子库存保存在世界数据里（`rbx_all_chests`），换存档不会带过去；但箱子物品本身带走内容。
- 「满箱」方块只作为外观状态自动切换，不注册到创造菜单，手动放置请用普通的 `随机箱子`。
- 刷新以游戏内日期为基准，睡觉跳过夜晚也会正常触发。
