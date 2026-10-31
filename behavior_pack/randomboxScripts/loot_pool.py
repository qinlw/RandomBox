# -*- coding: utf-8 -*-
"""随机奖池

奖池优先在运行时通过引擎接口生成：
    itemComp.GetLoadItems(flag)
        flag=False 仅返回创造栏物品（列表更干净，优先使用）
        flag=True  返回当前加载的全部物品（含其他模组，作为后备）
再经过滤后由 randomboxServer 缓存使用，因此可以自动包含当前游戏版本与
已加载模组的物品，无需手工维护清单。

- POOL：后备奖池（引擎接口不可用时使用），也可手工增删
- NON_STACKABLE：接口取不到最大堆叠数时的兜底判断
"""

import random

POOL = [
    # ---- 矿物 / 宝石 ----
    "minecraft:coal", "minecraft:charcoal", "minecraft:iron_ingot",
    "minecraft:copper_ingot", "minecraft:gold_ingot", "minecraft:redstone",
    "minecraft:lapis_lazuli", "minecraft:diamond", "minecraft:emerald",
    "minecraft:quartz", "minecraft:amethyst_shard", "minecraft:netherite_scrap",
    "minecraft:netherite_ingot", "minecraft:glowstone_dust", "minecraft:echo_shard",
    "minecraft:ancient_debris", "minecraft:nether_star",
    # ---- 食物 ----
    "minecraft:apple", "minecraft:golden_apple", "minecraft:enchanted_golden_apple",
    "minecraft:bread", "minecraft:cookie", "minecraft:cake", "minecraft:pumpkin_pie",
    "minecraft:cooked_beef", "minecraft:cooked_porkchop", "minecraft:cooked_chicken",
    "minecraft:cooked_mutton", "minecraft:cooked_rabbit", "minecraft:cooked_cod",
    "minecraft:cooked_salmon", "minecraft:carrot", "minecraft:golden_carrot",
    "minecraft:potato", "minecraft:baked_potato", "minecraft:beetroot",
    "minecraft:melon_slice", "minecraft:sweet_berries", "minecraft:glow_berries",
    "minecraft:chorus_fruit", "minecraft:honey_bottle", "minecraft:dried_kelp",
    "minecraft:milk_bucket",
    # ---- 种子 / 农作物 ----
    "minecraft:wheat_seeds", "minecraft:pumpkin_seeds", "minecraft:melon_seeds",
    "minecraft:beetroot_seeds", "minecraft:wheat", "minecraft:sugar_cane",
    "minecraft:sugar", "minecraft:cocoa_beans", "minecraft:nether_wart",
    "minecraft:egg", "minecraft:bamboo",
    # ---- 生物掉落 / 杂项 ----
    "minecraft:string", "minecraft:leather", "minecraft:feather", "minecraft:flint",
    "minecraft:gunpowder", "minecraft:slime_ball", "minecraft:ender_pearl",
    "minecraft:blaze_rod", "minecraft:blaze_powder", "minecraft:ghast_tear",
    "minecraft:magma_cream", "minecraft:phantom_membrane", "minecraft:rabbit_foot",
    "minecraft:rabbit_hide", "minecraft:ink_sac", "minecraft:glow_ink_sac",
    "minecraft:spider_eye", "minecraft:bone", "minecraft:bone_meal",
    "minecraft:rotten_flesh", "minecraft:shulker_shell", "minecraft:nautilus_shell",
    "minecraft:heart_of_the_sea", "minecraft:prismarine_shard",
    "minecraft:prismarine_crystals", "minecraft:dragon_breath",
    "minecraft:totem_of_undying", "minecraft:experience_bottle",
    # ---- 工具 / 武器 / 盔甲 ----
    "minecraft:iron_sword", "minecraft:iron_pickaxe", "minecraft:iron_axe",
    "minecraft:iron_shovel", "minecraft:iron_hoe",
    "minecraft:diamond_sword", "minecraft:diamond_pickaxe", "minecraft:diamond_axe",
    "minecraft:diamond_shovel", "minecraft:diamond_hoe",
    "minecraft:golden_sword", "minecraft:golden_pickaxe",
    "minecraft:bow", "minecraft:crossbow", "minecraft:arrow", "minecraft:shield",
    "minecraft:fishing_rod", "minecraft:shears", "minecraft:flint_and_steel",
    "minecraft:iron_helmet", "minecraft:iron_chestplate", "minecraft:iron_leggings",
    "minecraft:iron_boots", "minecraft:diamond_helmet", "minecraft:diamond_chestplate",
    "minecraft:diamond_leggings", "minecraft:diamond_boots", "minecraft:turtle_helmet",
    "minecraft:elytra", "minecraft:trident",
    # ---- 方块 / 其他 ----
    "minecraft:cobblestone", "minecraft:stone", "minecraft:dirt", "minecraft:sand",
    "minecraft:gravel", "minecraft:oak_log", "minecraft:birch_log",
    "minecraft:spruce_log", "minecraft:oak_planks", "minecraft:glass",
    "minecraft:obsidian", "minecraft:crying_obsidian", "minecraft:tnt",
    "minecraft:book", "minecraft:paper", "minecraft:compass", "minecraft:clock",
    "minecraft:name_tag", "minecraft:saddle", "minecraft:iron_horse_armor",
    "minecraft:golden_horse_armor", "minecraft:lead", "minecraft:torch",
    "minecraft:lantern", "minecraft:chest", "minecraft:crafting_table",
    "minecraft:furnace", "minecraft:bucket", "minecraft:water_bucket",
    "minecraft:lava_bucket", "minecraft:ender_chest", "minecraft:shulker_box",
    "minecraft:respawn_anchor", "minecraft:conduit", "minecraft:sea_lantern",
    "minecraft:glass_bottle", "minecraft:fermented_spider_eye",
    "minecraft:glistering_melon_slice", "minecraft:rabbit_stew",
]

NON_STACKABLE = {
    "minecraft:iron_sword", "minecraft:iron_pickaxe", "minecraft:iron_axe",
    "minecraft:iron_shovel", "minecraft:iron_hoe",
    "minecraft:diamond_sword", "minecraft:diamond_pickaxe", "minecraft:diamond_axe",
    "minecraft:diamond_shovel", "minecraft:diamond_hoe",
    "minecraft:golden_sword", "minecraft:golden_pickaxe",
    "minecraft:bow", "minecraft:crossbow", "minecraft:shield",
    "minecraft:fishing_rod", "minecraft:shears", "minecraft:flint_and_steel",
    "minecraft:iron_helmet", "minecraft:iron_chestplate", "minecraft:iron_leggings",
    "minecraft:iron_boots", "minecraft:diamond_helmet", "minecraft:diamond_chestplate",
    "minecraft:diamond_leggings", "minecraft:diamond_boots", "minecraft:turtle_helmet",
    "minecraft:elytra", "minecraft:trident",
    "minecraft:saddle", "minecraft:iron_horse_armor", "minecraft:golden_horse_armor",
    "minecraft:shulker_box",
}


# 本模组自己的方块不进奖池，避免“箱子开出另一个箱子”
OWN_ITEMS = ("randombox:random_box",)

# 技术/内部条目：不是正常可获得物品，抽到只会得到空气、流水之类
EXCLUDE_EXACT = {
    "air", "fire", "soul_fire", "water", "flowing_water", "lava", "flowing_lava",
    "barrier", "allow", "deny", "border_block", "invisible_bedrock",
    "command_block", "chain_command_block", "repeating_command_block",
    "jigsaw", "structure_block", "structure_void", "moving_block",
    "client_request_placeholder_block", "unknown", "camera", "end_portal",
    "end_gateway", "portal", "bubble_column", "light_block",
    "lit_furnace", "lit_blast_furnace", "lit_smoker", "lit_redstone_ore",
    "lit_deepslate_redstone_ore", "lit_redstone_lamp", "lit_pumpkin",
    "unlit_redstone_torch", "netherreactor", "glowingobsidian",
    # 教育版/化学
    "compound", "compound_creator", "material_reducer", "lab_table",
    "chemistry_table", "bleach", "ice_bomb", "rapid_fertilizer", "medicine",
    "chalkboard", "photo_item", "board", "portfolio", "balloon", "glow_stick",
    "sparkler", "underwater_tnt", "underwater_torch", "spawn_egg",
    # 旧版兼容 ID
    "wood", "log", "log2", "leaves", "leaves2", "sapling", "planks", "wool",
    "carpet", "concrete", "concrete_powder", "stained_glass",
    "stained_glass_pane", "stained_hardened_clay", "banner", "skull",
    "frame", "trip_wire", "waterlily", "noteblock", "grass_path", "stonebrick",
    "netherbrick", "monster_egg", "mob_spawner", "reeds", "double_plant",
}
EXCLUDE_PREFIX = ("element_", "light_block", "reserved", "info_update",
                  "deprecated_", "double_")
EXCLUDE_SUFFIX = ("_double_slab", "_standing_sign", "_wall_sign",
                 "_arm_collision")


try:
    _STRING_TYPES = (str, unicode)
except NameError:
    _STRING_TYPES = (str,)


def _Normalize(raw):
    """把接口返回的物品 ID 统一成带命名空间的字符串"""
    if not isinstance(raw, _STRING_TYPES):
        return ""
    name = raw.strip()
    if not name:
        return ""
    if ":" not in name:
        name = "minecraft:" + name
    return name


def BuildPool(itemIds):
    """过滤引擎返回的物品 ID 列表，生成可用奖池"""
    pool = []
    seen = set()
    for raw in itemIds or []:
        name = _Normalize(raw)
        if not name or name in seen or name in OWN_ITEMS:
            continue
        base = name.split(":", 1)[1]
        if base in EXCLUDE_EXACT:
            continue
        if base.startswith(EXCLUDE_PREFIX) or base.endswith(EXCLUDE_SUFFIX):
            continue
        seen.add(name)
        pool.append(name)
    return pool


def RollItem(pool=None, stackSize=None):
    """随机抽取一个物品，返回可直接用于 SpawnItemToPlayerInv 的物品字典

    pool:      奖池（为空时使用后备 POOL）
    stackSize: 可选回调，返回物品最大堆叠数；取不到时按 NON_STACKABLE 兜底
    """
    source = pool or POOL
    name = random.choice(source)
    limit = 0
    if stackSize is not None:
        try:
            limit = int(stackSize(name) or 0)
        except Exception:
            limit = 0
    if limit <= 0:
        limit = 1 if name in NON_STACKABLE else 3
    count = random.randint(1, min(3, limit))
    return {"newItemName": name, "newAuxValue": 0, "count": count}
