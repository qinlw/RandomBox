# -*- coding: utf-8 -*-
"""RandomBox 模组入口：注册服务端/客户端系统"""

from mod.common.mod import Mod
import mod.server.extraServerApi as serverApi
import mod.client.extraClientApi as clientApi

MOD_NAMESPACE = "randombox"


@Mod.Binding(name="RandomBoxMod", version="0.0.1")
class RandomBoxMod(object):
    def __init__(self):
        pass

    @Mod.InitServer()
    def initServer(self):
        serverApi.RegisterSystem(
            MOD_NAMESPACE,
            "RandomBoxServerSystem",
            "randomboxScripts.randomboxServer.RandomBoxServerSystem",
        )

    @Mod.InitClient()
    def initClient(self):
        clientApi.RegisterSystem(
            MOD_NAMESPACE,
            "RandomBoxClientSystem",
            "randomboxScripts.randomboxClient.RandomBoxClientSystem",
        )
