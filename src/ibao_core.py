"""ibao-ST farming core adapted for XuanShu managed clients.
Source supplied by user, 2026-09-11. Original credits: Hailtothethrone,
Nitsuj, Ultimate, Lxghtend, sydu8, ibao. Login/restart code is excluded.
Each run receives its own function globals; no module state is shared.
"""
import asyncio
import copy
import re
from time import time
from wizwalker import Client, XYZ
from wizwalker.constants import Keycode
from wizwalker.errors import ClientClosedError, HookNotActive
from wizwalker.memory import Window
from loguru import logger


class CharacterSelectionError(RuntimeError):
    """Selection could not be verified; never enter an arbitrary character."""

class clientInfo:

    def __init__(self, username: str, password: str, handle, title: str, wizLst: list, totalAzothCollected: int, timeSinceBotAction: int):
        self.startTime = time()
        self.username = username
        self.password = password
        self.handle = handle
        self.title = title
        self.wizLst = wizLst
        self.totalAzothCollected = totalAzothCollected
        self.timeSinceBotAction = timeSinceBotAction
        self.last_restart_time = time()

class wizardInfo:

    def __init__(self, wizardName: str, wizardLevel: str, wizardLocation: str, currentHappiness: int, totalHappiness: int, wizardAzoth: int):
        self.Name = wizardName
        self.Level = wizardLevel
        self.Location = wizardLocation
        self.Happiness = currentHappiness
        self.totalHappiness = totalHappiness
        self.Azoth = wizardAzoth

    def __str__(self):
        return f'{removeTags(self.Name)} the {removeTags(self.Level)} in {removeTags(self.Location)}'

    def __eq__(self, other):
        return self.Name == other.Name and self.Level == other.Level
activeClients = []
reagents = ['Wood', 'Stone', 'Mushroom', 'Ore', 'Cattail', 'Mandrake', 'Parchment', 'Scraplron', 'Black Lotus', 'LavaLilly', 'Frost Flower', 'Kelp', 'Pearl', 'Sandstone', 'Shell', 'Agave', 'CometTail', 'Stardust', 'Antiquitie', 'Fulgurite', 'AetherDust', 'AetherOre', 'Artifacts', 'Polygons', 'FrostedFlax_01']
snackCard0 = ['WorldView', 'PetFeedForHappinessWindow', 'wndBkgBottom', 'wndCards', 'chkSnackCard0']
petSystem = ['WorldView', 'windowHUD', 'PetSystemButton']
closeFeedPetWindow = ['WorldView', 'PetFeedForHappinessWindow', 'CloseFeedPetForHappinessWindow']
feedStack = ['WorldView', 'PetFeedForHappinessWindow', 'FeedStackOfSnacksButton']
happinessText = ['WorldView', 'PetFeedForHappinessWindow', 'HappinessText']
feedPet = ['WorldView', 'windowHUD', 'PetSystemButton', 'PetButtonLayout', 'FeedPetButton']
petPowerButton = ['WorldView', 'windowHUD', 'PetSystemButton', 'PetButtonLayout', 'UsePetPowerButton']
petPowerCooldown = ['WorldView', 'windowHUD', 'PetSystemButton', 'PetButtonLayout', 'UsePetPowerButton', 'PetPowerCooldownText']
PET_POWER_COOLDOWN_THRESHOLD = 30
PET_POWER_TIMEOUT = 30
quitButton = ['WorldView', 'DeckConfiguration', 'SettingPage', 'QuitButton']
logOutConfirm = ['MessageBoxModalWindow', 'messageBoxBG', 'messageBoxLayout', 'AdjustmentWindow', 'Layout', 'centerButton']
txtLocation = ['WorldView', 'mainWindow', 'sprSubBanner', 'txtLocation']
txtLevel = ['WorldView', 'mainWindow', 'sprSubBanner', 'txtLevel']
txtName = ['WorldView', 'mainWindow', 'sprBanner', 'txtName']
playButton = ['WorldView', 'mainWindow', 'btnPlay']
chatWindowPath = ['WorldView', 'WizardChatBox', 'chatContainer', 'chatLogContainer', 'chatLogInnerContainer', 'chatLog']
rightClassRoomButton = ['WorldView', 'mainWindow', 'RightClassRoomButton']
leftClassRoomButton = ['WorldView', 'mainWindow', 'LeftClassRoomButton']
cardCount = ['WorldView', 'DeckConfiguration', 'DeckConfigurationWindow', 'ControlSprite', 'DeckPage', 'TreasureCardCount']
deckWindow = ['WorldView', 'DeckConfiguration', 'DeckConfigurationWindow']
treasureCardButton = ['WorldView', 'DeckConfiguration', 'DeckConfigurationWindow', 'ControlSprite', 'TreasureCardButton']
deckCloseButton = ['WorldView', 'DeckConfiguration', 'Close_Button']
gameplayOptions = ['WorldView', 'DeckConfiguration', 'SettingPage', 'TabWindow', 'AdvGameplayButton']
hiddenToFriendValue = ['WorldView', 'DeckConfiguration', 'SettingPage', 'AdvGameplayOptions', 'OptionInvisibleToFriends', 'OptionControl', 'OptionValue']
hiddenToFriendRightBttn = ['WorldView', 'DeckConfiguration', 'SettingPage', 'AdvGameplayOptions', 'OptionInvisibleToFriends', 'OptionControl', 'OptionButtonRight']
settingsConfirm = ['WorldView', 'DeckConfiguration', 'SettingPage', 'OkButton']

async def portallst(client, is_sunken_city=False):
    """动态探测优先，失败后回退到预设坐标"""
    current_pos = await client.body.position()
    current_pos.z -= 500
    await client.teleport(current_pos)
    entity_list = await client.get_base_entity_list()
    for entity in entity_list:
        if await entity.object_name() in reagents:
            reagent_pos = await entity.location()
            reagent_pos.z -= 0
            await client.teleport(reagent_pos)
            print(f'[{client.title}] 动态探测到试剂')
            return True
locationList = []
baseLocationList = []

async def window_from_path(base_window: Window, path: list[str]) -> Window:
    if not path:
        return base_window
    for child in await base_window.children():
        if await child.name() == path[0]:
            if (found_window := (await window_from_path(child, path[1:]))):
                return found_window
    return False

async def is_visible_by_path(base_window: Window, path: list[str]):
    if (window := (await window_from_path(base_window, path))):
        return await window.is_visible()
    return False

async def click_window_from_path(mouse_handler, base_window, path):
    try:
        await mouse_handler.click_window(await window_from_path(base_window, path))
    except Exception:
        pass

async def click_window_until_gone(client, path):
    while (window := (await window_from_path(client.root_window, path))) and await is_visible_by_path(client.root_window, path):
        await asyncio.sleep(0.02)
        await client.mouse_handler.click_window(window)
        await asyncio.sleep(0.1)

async def petPower(client, delay=2):
    await asyncio.sleep(0.2)
    await click_window_from_path(client.mouse_handler, client.root_window, petPowerButton)

def removeTags(string):
    return string.replace('</center>', '').replace('<center>', '')

def removeTitle(string):
    return string.replace('AzothFarm: ', '')

async def nearestReagent(client, title):
    entityList = await client.get_base_entity_list()
    reagentList = []
    for entity in entityList:
        if await entity.object_name() in reagents:
            print(f'[{removeTitle(title)}] Reagent Detected:', await entity.object_name())
            reagentList += [entity]
    if len(reagentList) == 0:
        return (False, None)
    else:
        smallestDistance = 999999999.0
        clientLocation = await client.body.position()
        for reagent in reagentList:
            reagentLocation = await reagent.location()
            currentDistance = clientLocation - reagentLocation
            if currentDistance < smallestDistance:
                smallestDistance = currentDistance
                closest = reagentLocation
        return (True, closest)

async def setup(client):
    await asyncio.sleep(0)

async def petPowerVisibility(client):
    return await is_visible_by_path(client.root_window, petPowerButton)

async def cooldownVisibility(client):
    return await is_visible_by_path(client.root_window, petPowerCooldown)

async def crownshopVisibilty(client):
    try:
        return await (await client.root_window.get_windows_with_name('permanentShop'))[0].is_visible()
    except Exception:
        return False

async def appearOffline(client: Client, title, wizard):
    while not await is_visible_by_path(client.root_window, settingsConfirm):
        await asyncio.sleep(0.02)
        await client.send_key(Keycode.ESC, 0.1)
        await asyncio.sleep(0.1)
    await click_window_from_path(client.mouse_handler, client.root_window, gameplayOptions)
    try:
        if removeTags(await (await window_from_path(client.root_window, hiddenToFriendValue)).maybe_text()) == 'No':
            print(f'[{title}]: {removeTags(wizard.Name)} appearing offline...')
            await click_window_from_path(client.mouse_handler, client.root_window, hiddenToFriendRightBttn)
            await asyncio.sleep(0.1)
    except AttributeError:
        while await is_visible_by_path(client.root_window, playButton):
            await asyncio.sleep(0.02)
            await click_window_from_path(client.mouse_handler, client.root_window, playButton)
        while await client.is_loading():
            await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
        while await crownshopVisibilty(client):
            await asyncio.sleep(0.02)
            await asyncio.sleep(1)
            await client.send_key(Keycode.ESC, 0.3)
            await asyncio.sleep(0.4)
            await client.send_key(Keycode.ESC, 0.3)
            await asyncio.sleep(1)
        await appearOffline(client, title, wizard)
    while await is_visible_by_path(client.root_window, settingsConfirm):
        await asyncio.sleep(0.02)
        await click_window_from_path(client.mouse_handler, client.root_window, settingsConfirm)

async def skipDialogue(client):
    while True:
        await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        while await client.is_in_dialog():
            await asyncio.sleep(0.15)
            await client.send_key(Keycode.SPACEBAR, 0.05)
        while await crownshopVisibilty(client):
            await asyncio.sleep(0.02)
            await asyncio.sleep(1)
            await client.send_key(Keycode.ESC, 0.3)
            await asyncio.sleep(1)
            await client.send_key(Keycode.ESC, 0.3)
            await asyncio.sleep(1)

async def azothCollect(client, tipAmount):
    chat = await window_from_path(client.root_window, chatWindowPath)
    before = await chat.maybe_text() if chat else ''
    while await petPowerVisibility(client):
        await asyncio.sleep(0.02)
        await petPower(client, 0.5)
    while True:
        chat = await window_from_path(client.root_window, chatWindowPath)
        text = await chat.maybe_text() if chat else ''
        new_text = text[len(before):] if text and text.startswith(before or '') else ''
        if 'You received: Azoth' in new_text or ('万灵秘药' in new_text and re.search(r'获得|获取|收到', new_text)):
            return
        await asyncio.sleep(0.02)
        await petPower(client, 0.5)
        await asyncio.sleep(0.5)

async def snackVisibility(client):
    return await is_visible_by_path(client.root_window, snackCard0)

async def azothFarmer(p, listPosition):
    dialogueChecker = None
    try:
        config = get_config()
        enable_page_turning = config['ENABLE_PAGE_TURNING']
        await setup(p)
        try:
            dialogueChecker = asyncio.create_task(skipDialogue(p))
        finally:
            pass
        startTime = time()
        while not await is_visible_by_path(p.root_window, playButton):
            await asyncio.sleep(0.02)
            await p.send_key(Keycode.TAB, 0.1)
        while not removeTags(str(await (await window_from_path(p.root_window, txtLocation)).maybe_text())) in baseLocationList:
            await asyncio.sleep(0.02)
            await p.send_key(Keycode.TAB, 0.1)
            await asyncio.sleep(0.9)
        await asyncio.sleep(1.2)
        if enable_page_turning:
            if await is_visible_by_path(p.root_window, leftClassRoomButton):
                await click_window_until_gone(p, leftClassRoomButton)
        wizard = wizardInfo(await (await window_from_path(p.root_window, txtName)).maybe_text(), await (await window_from_path(p.root_window, txtLevel)).maybe_text(), await (await window_from_path(p.root_window, txtLocation)).maybe_text(), 0, 0, 0)
        if enable_page_turning:
            if await is_visible_by_path(p.root_window, rightClassRoomButton):
                await click_window_until_gone(p, rightClassRoomButton)
        while not wizard in [wiz for wiz in activeClients[listPosition].wizLst]:
            await asyncio.sleep(0.02)
            if removeTags(wizard.Location) in baseLocationList:
                activeClients[listPosition].wizLst += [copy.deepcopy(wizard)]
            await p.send_key(Keycode.TAB, 0)
            wizard = wizardInfo(await (await window_from_path(p.root_window, txtName)).maybe_text(), await (await window_from_path(p.root_window, txtLevel)).maybe_text(), await (await window_from_path(p.root_window, txtLocation)).maybe_text(), 0, 0, 0)
        print(f'[{activeClients[listPosition].title}] Is using these wizards:')
        for x in activeClients[listPosition].wizLst:
            print(x)
        if await is_visible_by_path(p.root_window, leftClassRoomButton):
            if enable_page_turning:
                await click_window_until_gone(p, leftClassRoomButton)
        if removeTags(str(await (await window_from_path(p.root_window, txtLocation)).maybe_text())) in baseLocationList:
            await click_window_until_gone(p, playButton)
        elif not removeTags(str(await (await window_from_path(p.root_window, txtLocation)).maybe_text())) in baseLocationList:
            if await is_visible_by_path(p.root_window, rightClassRoomButton):
                await click_window_until_gone(p, rightClassRoomButton)
            await p.send_key(Keycode.TAB)
        if await is_visible_by_path(p.root_window, playButton):
            await click_window_until_gone(p, playButton)
        await asyncio.sleep(8.5)
        if await is_visible_by_path(p.root_window, quitButton):
            await p.send_key(Keycode.ESC, 0.1)
        originalWizards = len(activeClients[listPosition].wizLst)
        runthrough = 0
        while True:
            await asyncio.sleep(0.02)
            if len(activeClients[listPosition].wizLst) == 0:
                break
            for position, wizard in enumerate(activeClients[listPosition].wizLst, 0):
                needSwitch = False
                if runthrough == 0:
                    while await crownshopVisibilty(p):
                        await asyncio.sleep(0.02)
                        await asyncio.sleep(1)
                        await p.send_key(Keycode.ESC, 0.3)
                        await asyncio.sleep(1)
                        await p.send_key(Keycode.ESC, 0.3)
                        await asyncio.sleep(1)
                    while not await is_visible_by_path(p.root_window, feedPet):
                        await asyncio.sleep(0.02)
                        await click_window_from_path(p.mouse_handler, p.root_window, petSystem)
                    while not await is_visible_by_path(p.root_window, closeFeedPetWindow):
                        await asyncio.sleep(0.02)
                        await p.mouse_handler.click_window_with_name('FeedPetButton')
                    petHappinessText = await window_from_path(p.root_window, happinessText)
                    Happiness, totalHappiness = removeTags(await petHappinessText.maybe_text()).split('/')
                    wizard.Happiness, wizard.totalHappiness = (int(Happiness), int(totalHappiness))
                    await click_window_until_gone(p, closeFeedPetWindow)
                    while await is_visible_by_path(p.root_window, feedPet):
                        await asyncio.sleep(0.02)
                        await click_window_from_path(p.mouse_handler, p.root_window, petSystem)
                    while not await is_visible_by_path(p.root_window, deckWindow):
                        await asyncio.sleep(0.02)
                        await p.send_key(Keycode.P)
                        await asyncio.sleep(0.1)
                    while not await is_visible_by_path(p.root_window, cardCount):
                        await asyncio.sleep(0.02)
                        await click_window_from_path(p.mouse_handler, p.root_window, treasureCardButton)
                        await asyncio.sleep(0.1)
                    window: Window = await window_from_path(p.root_window, cardCount)
                    if window:
                        cardCountText = await window.maybe_text()
                        cardCountText = cardCountText.replace('<center>', '').replace('</center>', '').replace('/999', '')
                        wizard.Azoth = int(cardCountText)
                    await click_window_until_gone(p, deckCloseButton)
                    await appearOffline(p, activeClients[listPosition].title, wizard)
                while not needSwitch:
                    await asyncio.sleep(0.02)
                    if await crownshopVisibilty(p):
                        await asyncio.sleep(1)
                        await p.send_key(Keycode.ESC, 0.3)
                        await asyncio.sleep(1)
                        await p.send_key(Keycode.ESC, 0.3)
                        await asyncio.sleep(1)
                    print(f'[{activeClients[listPosition].title}]: {removeTags(wizard.Name)} has {wizard.Happiness} happiness')
                    print(f'[{activeClients[listPosition].title}]: {removeTags(wizard.Name)} has {wizard.Azoth} azoth')
                    keepWizard = True
                    if int(wizard.Happiness) < 5:
                        print(f'[{activeClients[listPosition].title}] Feeding Pet')
                        keepWizard = await refillhappiness(p)
                        wizard.Happiness = wizard.totalHappiness
                    if int(wizard.Azoth) == 999:
                        keepWizard = False
                    if keepWizard:
                        locationIndex = baseLocationList.index(removeTags(wizard.Location))
                        if not locationList[locationIndex][1] == 'Dungeon':
                            await asyncio.sleep(0.3)
                        else:
                            location = await p.zone_name()
                            while location == await p.zone_name():
                                await asyncio.sleep(0.02)
                                while location == await p.zone_name():
                                    await asyncio.sleep(0.02)
                                    if await crownshopVisibilty(p):
                                        await asyncio.sleep(1)
                                        await p.send_key(Keycode.ESC, 0.3)
                                        await asyncio.sleep(1)
                                        await p.send_key(Keycode.ESC, 0.3)
                                        await asyncio.sleep(1)
                                    await p.send_key(Keycode.X, 0.1)
                                while await p.is_loading():
                                    await asyncio.sleep(0.02)
                                    await p.send_key(Keycode.X, 0.1)
                        for tpLocation in locationList[locationIndex][2]:
                            bodyPosition = await p.body.position()
                            bodyPosition.z = bodyPosition.z - 500
                            await asyncio.sleep(0.2)
                            await p.teleport(bodyPosition)
                            if tpLocation == None:
                                pass
                            else:
                                await asyncio.sleep(0.2)
                                await p.teleport(XYZ(tpLocation.x, tpLocation.y, tpLocation.z - 500))
                                await asyncio.sleep(0.2)
                            while True:
                                await asyncio.sleep(0.02)
                                try:
                                    reagentDetected, reagentLocation = await nearestReagent(p, activeClients[listPosition].title)
                                    break
                                except Exception:
                                    pass
                            if reagentDetected and keepWizard:
                                reagentLocation.z = reagentLocation.z - 500
                                await p.teleport(reagentLocation)
                                max_retries = 2
                                current_retry = 0
                                pet_power_visible = False
                                while current_retry < max_retries and (not pet_power_visible):
                                    await asyncio.sleep(0.02)
                                    start_time = time()
                                    pet_power_visible = False
                                    position_reset = False
                                    print(f'[{activeClients[listPosition].title}] 等待宠物能量按钮出现（尝试 {current_retry + 1}/{max_retries}）...')
                                    while not (pet_power_visible := (await petPowerVisibility(p))):
                                        await asyncio.sleep(0.02)
                                        if time() - start_time > 5 and (not position_reset):
                                            print(f'[{activeClients[listPosition].title}] 5秒未检测到宠物能量按钮，尝试重置位置...')
                                            original_z = reagentLocation.z
                                            reagentLocation.z = 0
                                            await p.teleport(reagentLocation)
                                            await asyncio.sleep(2)
                                            reagentLocation.z = original_z
                                            await p.teleport(reagentLocation)
                                            position_reset = True
                                            continue
                                        if time() - start_time > PET_POWER_TIMEOUT:
                                            print(f'[{activeClients[listPosition].title}] ⚠️ 等待宠物能量按钮超时({PET_POWER_TIMEOUT}s)')
                                            break
                                        await asyncio.sleep(1)
                                    if not pet_power_visible and current_retry < max_retries - 1:
                                        current_retry += 1
                                        print(f'[{activeClients[listPosition].title}] 重新尝试采集（{current_retry}/{max_retries}）...')
                                        await logout_and_in(p, wizard, True, activeClients[listPosition].title)
                                        await p.teleport(reagentLocation)
                                        await asyncio.sleep(3)
                                    else:
                                        break
                                if not pet_power_visible:
                                    print(f'[{activeClients[listPosition].title}] 所有重试失败，切换角色')
                                    print(f'[{activeClients[listPosition].title}] 保护机制触发，重新检查角色状态...')
                                    while not await is_visible_by_path(p.root_window, feedPet):
                                        await asyncio.sleep(0.02)
                                        await click_window_from_path(p.mouse_handler, p.root_window, petSystem)
                                    while not await is_visible_by_path(p.root_window, closeFeedPetWindow):
                                        await asyncio.sleep(0.02)
                                        await p.mouse_handler.click_window_with_name('FeedPetButton')
                                    petHappinessText = await window_from_path(p.root_window, happinessText)
                                    Happiness, totalHappiness = removeTags(await petHappinessText.maybe_text()).split('/')
                                    wizard.Happiness, wizard.totalHappiness = (int(Happiness), int(totalHappiness))
                                    await click_window_until_gone(p, closeFeedPetWindow)
                                    while not await is_visible_by_path(p.root_window, deckWindow):
                                        await asyncio.sleep(0.02)
                                        await p.send_key(Keycode.P)
                                        await asyncio.sleep(0.1)
                                    while not await is_visible_by_path(p.root_window, cardCount):
                                        await asyncio.sleep(0.02)
                                        await click_window_from_path(p.mouse_handler, p.root_window, treasureCardButton)
                                        await asyncio.sleep(0.1)
                                    window: Window = await window_from_path(p.root_window, cardCount)
                                    if window:
                                        cardCountText = await window.maybe_text()
                                        cardCountText = cardCountText.replace('<center>', '').replace('</center>', '').replace('/999', '')
                                        wizard.Azoth = int(cardCountText)
                                    await click_window_until_gone(p, deckCloseButton)
                                    print(f'[{activeClients[listPosition].title}] 重新检查: {removeTags(wizard.Name)} - 快乐度: {wizard.Happiness}, Azoth: {wizard.Azoth}')
                                    needSwitch = True
                                    break
                                needSwitch = True
                                wizard.Happiness -= 5
                                while not await petPowerVisibility(p):
                                    await asyncio.sleep(0.02)
                                    await asyncio.sleep(0.2)
                                while await cooldownVisibility(p):
                                    await asyncio.sleep(0.02)
                                    await asyncio.sleep(0.2)
                                tiplen = len(await p.root_window.get_windows_with_name('TipWindow'))
                                try:
                                    await asyncio.wait_for(azothCollect(p, tiplen), 8)
                                    activeClients[listPosition].totalAzothCollected += 1
                                    wizard.Azoth += 1
                                    report_collection()
                                except Exception:
                                    print(print(f'[{activeClients[listPosition].title}]-failsafe activated, quit without azoth'))
                                break
                    try:
                        nextWizard = activeClients[listPosition].wizLst[position + 1]
                    except IndexError:
                        nextWizard = activeClients[listPosition].wizLst[0]
                    await asyncio.sleep(0.2)
                    if not keepWizard:
                        needSwitch = True
                    await logout_and_in(p, nextWizard if needSwitch else wizard, needSwitch, activeClients[listPosition].title)
                    if not keepWizard:
                        break
                    print('------------------------------------------------------')
                    print('Azoth收集总量: ', sum([x.totalAzothCollected for x in activeClients]))
                    print('运行时间: ', activeClients[listPosition].timeSinceBotAction, '秒')
                    print(f'总运行时间: {round((time() - activeClients[listPosition].startTime) / 60, 2)} 分钟')
                    print(f'宠物能量检测: 超时{PET_POWER_TIMEOUT}s | 冷却阈值{PET_POWER_COOLDOWN_THRESHOLD}s')
                    print('------------------------------------------------------')
                    activeClients[listPosition].timeSinceBotAction = 0
                if not keepWizard:
                    activeClients[listPosition].timeSinceBotAction = 0
                    print(f'[{activeClients[listPosition].title}] Removing Wizard From List: {wizard}')
                    if len(activeClients[listPosition].wizLst) != 1:
                        newFirst = activeClients[listPosition].wizLst[position:]
                        newLast = activeClients[listPosition].wizLst[:position]
                        activeClients[listPosition].wizLst = newFirst + newLast
                        activeClients[listPosition].wizLst.pop(position)
                    else:
                        activeClients[listPosition].wizLst.pop(position)
                        pass
                    break
            if keepWizard:
                runthrough += 1
    finally:
        if dialogueChecker is not None:
            dialogueChecker.cancel()
            await asyncio.gather(dialogueChecker, return_exceptions=True)

async def azothCheck(p):
    e = 0
    c = await p.root_window.get_windows_with_name('TipWindow')
    for x in c:
        try:
            for y in await x.children():
                if await y.name() == 'ControlSprite':
                    e += 1
        except Exception:
            await asyncio.sleep(0.1)
    if e > 0:
        return True
    else:
        return False

async def logout_and_in(client, nextWizard, needSwitch, title):
    config = get_config()
    enable_page_turning = config['ENABLE_PAGE_TURNING']
    character_switch_delay = config['CHARACTER_SWITCH_DELAY']
    print(f'[{title}] 正在登出和登入')
    next_escape = 0
    loop = asyncio.get_running_loop()
    menu_remaining, loading_remaining = 15.0, 60.0
    loading = False
    while True:
        if menu_remaining <= 0:
            raise CharacterSelectionError(f'{title} 登出超时，未到达选角界面；自动恢复')
        if loading_remaining <= 0:
            raise CharacterSelectionError(f'{title} 登出加载超时，未到达选角界面；自动恢复')
        started = loop.time()
        try:
            # Loading has its own cumulative allowance, not a fresh timeout on
            # every poll. A hung read/click is still bounded by the active budget.
            async with asyncio.timeout(loading_remaining if loading else menu_remaining):
                path, window = None, None
                try:
                    loading = await client.is_loading()
                    if not loading:
                        if await is_visible_by_path(client.root_window, playButton):
                            break
                        if await is_visible_by_path(client.root_window, logOutConfirm):
                            path = logOutConfirm
                        elif await is_visible_by_path(client.root_window, quitButton):
                            path = quitButton
                        if path is not None:
                            window = await window_from_path(client.root_window, path)
                            if not window or not await window.is_visible():
                                window = None
                            elif await is_visible_by_path(client.root_window, playButton):
                                window = None
                            elif path == quitButton and await is_visible_by_path(client.root_window, logOutConfirm):
                                window = None  # A modal appeared after the first read.
                            loading = await client.is_loading()
                except (HookNotActive, ClientClosedError):
                    raise  # Missing connection is not a transient UI read.
                except Exception as exc:
                    logger.debug('ibao {} 登出界面暂不可读，重试：{}', title, exc)
                    await asyncio.sleep(.12)
                    continue
                if not loading:
                    if window is not None:
                        # One click per observation; Quit can stay visible under
                        # the modal, so do not wait for that background button.
                        await client.mouse_handler.click_window(window)
                        next_escape = loop.time() + 1
                    elif path is None and loop.time() >= next_escape:
                        await client.send_key(Keycode.ESC, .3)
                        next_escape = loop.time() + 1
                await asyncio.sleep(.12)
        except TimeoutError as exc:
            raise CharacterSelectionError(f'{title} 登出超时，未到达选角界面；自动恢复') from exc
        finally:
            elapsed = loop.time() - started
            if loading:
                loading_remaining -= elapsed
            else:
                menu_remaining -= elapsed
    if needSwitch:
        print(f'[{title}] 正在切换魔法师到: {nextWizard}')
    async def selected_wizard():
        name = await (await window_from_path(client.root_window, txtName)).maybe_text()
        level = await (await window_from_path(client.root_window, txtLevel)).maybe_text()
        location = await (await window_from_path(client.root_window, txtLocation)).maybe_text()
        if not removeTags(name).strip() or not removeTags(level).strip():
            raise ValueError('选角信息尚未就绪')
        return wizardInfo(name, level, location, 0, 0, 0)

    entered = False
    # Retry locally before escalation. The deadline scales with the user's
    # Tab interval instead of expiring after 4 seconds during a slow key press.
    for attempt in range(3):
        deadline = asyncio.get_running_loop().time() + max(4, character_switch_delay * 8 + 1)
        seen = set()
        while asyncio.get_running_loop().time() < deadline:
            try:
                wizard = await selected_wizard()
                if wizard == nextWizard:
                    # Fresh read immediately before Play; no stale match after
                    # a page turn or failed read, and no second Play call below.
                    if await selected_wizard() == nextWizard:
                        await click_window_until_gone(client, playButton)
                        entered = True
                        break
                if not needSwitch:
                    break  # Same-character reload must also verify its target.
                right = enable_page_turning and await is_visible_by_path(client.root_window, rightClassRoomButton)
                left = enable_page_turning and await is_visible_by_path(client.root_window, leftClassRoomButton)
                page = 1 if left and not right else 0
                key = (page, wizard.Name, wizard.Level)
                if key in seen:
                    other_page = 1 - page
                    if (right or left) and not any(item[0] == other_page for item in seen):
                        await click_window_until_gone(client, rightClassRoomButton if right else leftClassRoomButton)
                        await asyncio.sleep(character_switch_delay)
                        continue
                    break  # A complete cycle without the target; rescan.
                seen.add(key)
                await client.send_key(Keycode.TAB, min(.1, character_switch_delay))
                await asyncio.sleep(max(0, character_switch_delay - .1))
            except Exception as exc:
                logger.debug('ibao {} 选角信息暂不可用：{}', title, exc)
                await asyncio.sleep(max(.1, character_switch_delay))
        if entered:
            break
        logger.warning('ibao {} 选角未确认，自动重新扫描（{}/3）：{}', title, attempt + 1, nextWizard)
        await asyncio.sleep(character_switch_delay)
    if not entered:
        raise CharacterSelectionError(f'{title} 未确认目标角色 {nextWizard}；未点击进入，自动恢复')
    await client.wait_for_zone_change()
    await asyncio.sleep(0.5)
    if await is_visible_by_path(client.root_window, quitButton):
        await client.send_key(Keycode.ESC, 0.1)

async def refillhappiness(p):
    while not await is_visible_by_path(p.root_window, feedPet):
        await asyncio.sleep(0.02)
        await click_window_from_path(p.mouse_handler, p.root_window, petSystem)
    while not await is_visible_by_path(p.root_window, closeFeedPetWindow):
        await asyncio.sleep(0.02)
        await p.mouse_handler.click_window_with_name('FeedPetButton')
    happiness = removeTags(await (await window_from_path(p.root_window, happinessText)).maybe_text())
    while len(happiness.split('/')) != 2 or int(happiness.split('/')[0]) < int(happiness.split('/')[1]):
        await asyncio.sleep(0.02)
        await click_window_from_path(p.mouse_handler, p.root_window, snackCard0)
        await click_window_from_path(p.mouse_handler, p.root_window, feedStack)
        happiness = removeTags(await (await window_from_path(p.root_window, happinessText)).maybe_text())
        if not await snackVisibility(p):
            await click_window_until_gone(p, closeFeedPetWindow)
            while await is_visible_by_path(p.root_window, feedPet):
                await asyncio.sleep(0.02)
                await click_window_from_path(p.mouse_handler, p.root_window, petSystem)
            return False
    await click_window_until_gone(p, closeFeedPetWindow)
    while await is_visible_by_path(p.root_window, feedPet):
        await asyncio.sleep(0.02)
        await click_window_from_path(p.mouse_handler, p.root_window, petSystem)
    return True
