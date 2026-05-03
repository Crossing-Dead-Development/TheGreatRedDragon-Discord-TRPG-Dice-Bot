import discord
from discord import app_commands
from discord.ext import commands
import random
import re
import asyncio
import threading
import pystray
from PIL import Image
import sys
import os

# --- 1. 路徑與資源處理 ---
def resource_path(relative_path):
    """ 強制定位資源路徑，確保打包後圖標不失蹤 """
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)

# --- 按鈕類別 ---
class DiceButton(discord.ui.Button):
    def __init__(self, label, details):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.details = details

    async def callback(self, interaction: discord.Interaction):
        # 確保內容不超過 Discord 的 2000 字限制
        content = self.details if len(self.details) < 2000 else self.details[:1990] + "..."
        await interaction.response.send_message(content, ephemeral=False)

class DiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
    async def on_timeout(self):
        for item in self.children: item.disabled = True

class DiceButton(discord.ui.Button):
    # 新增一個 style 參數，預設為藍色 (primary)
    def __init__(self, label, details, style=discord.ButtonStyle.primary):
        super().__init__(label=label, style=style)
        self.details = details

    async def callback(self, interaction: discord.Interaction):
        content = self.details if len(self.details) < 2000 else self.details[:1990] + "..."
        await interaction.response.send_message(content, ephemeral=False)

class SecretDiceView(discord.ui.View):
    def __init__(self, gm_user, result_text):
        super().__init__(timeout=600)
        self.gm_user = gm_user
        self.result_text = result_text

    @discord.ui.button(label="傳送結果給 GM", style=discord.ButtonStyle.danger)
    async def send_to_gm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # 傳送私訊給指定 GM
            await self.gm_user.send(f"來自 {interaction.user.name} 的暗骰結果：\n{self.result_text}")
            button.disabled = True
            button.label = "已傳送給 GM"
            await interaction.response.edit_message(view=self)
        except discord.Forbidden:
            await interaction.response.send_message("無法傳送私訊給該 GM，請確認對方已開啟私訊功能。", ephemeral=True)

# --- 2. 核心計算引擎 ---
def evaluate_expr(expr):
    """解析並計算算式，回傳 (總和, 詳細過程)"""
    
    def evaluate_base(sub_expr):
        sub_expr = sub_expr.replace(" ", "")
        display_det = sub_expr 
        calc_expr = sub_expr
        
        # 1. 處理 NdS 骰子格式 (例如 1d100)
        dice_found = re.findall(r'(\d+)d(\d+)', sub_expr)
        for n, sd in dice_found:
            rolls = [random.randint(1, int(sd)) for _ in range(int(n))]
            s = sum(rolls)
            roll_str = f"[{'+'.join(map(str, rolls))}]"
            display_det = display_det.replace(f"{n}d{sd}", roll_str, 1)
            calc_expr = calc_expr.replace(f"{n}d{sd}", f"({s})", 1)
        
        try:
            safe_expr = re.sub(r'[^\d\+\-\*\/\(\)\.]', '', calc_expr)
            final_v = eval(safe_expr)
            return int(final_v), display_det
        except:
            return 0, sub_expr

    # --- 修正後的統一處理邏輯 ---
    # 先處理乘法語法補完
    normalized_expr = re.sub(r'(\d+)\(', r'\1*(', expr)
    
    # 使用一個變數來保存最終的顯示過程，避免多次擲骰
    if "(" not in normalized_expr and "d" in normalized_expr:
        return evaluate_base(normalized_expr)
    
    # 如果有括號 (如 3(1d3))，則需要遞迴解析
    current_expr_for_calc = normalized_expr
    current_expr_for_show = expr
    
    while "(" in current_expr_for_calc:
        match = re.search(r'\(([^()]+)\)', current_expr_for_calc)
        if not match: break
        
        inner_content = match.group(1)
        val, det = evaluate_base(inner_content)
        
        # 計算用：替換成純數字
        current_expr_for_calc = current_expr_for_calc.replace(f"({inner_content})", str(val), 1)
        # 顯示用：替換成過程 det
        current_expr_for_show = current_expr_for_show.replace(f"({inner_content})", f"{det}", 1)

    # 最終結果計算
    final_val, _ = evaluate_base(current_expr_for_calc)
    _, final_det = evaluate_base(current_expr_for_show)
    
    return final_val, final_det
    
# --- 4. 機器人主體與設定---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True 
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print(f"已同步斜線指令")

# --- 完整 CoC 7版 狂氣症狀資料庫 ---
MADNESS_DATA = {
    "RT": {
        1: "失憶：失去離開前一個安全地方後的所有記憶。例如覺得自己正在吃早餐，然後就和一隻怪物對峙。",
        2: "心因性殘障：出現因為心理原因的失明，失聰，又或是部份或全部的四肢殘障。",
        3: "狂暴：憤怒／興奮得血往上湧，失去判斷力，對周圍敵人朋友甚至物品進行無差別的攻擊行為。",
        4: "疑神疑鬼：出現嚴重的被害妄想 -「人人都想捉住我」「每個人都不能信任」「他們在監視著我」「我們中出了叛徒」「全部都是騙局」。",
        5: "關係依賴：把在場的一個人誤認成自己非常重要的人。請細心考慮扮演方式。參考背景條目「重要的人」。",
        6: "昏厥：陷入昏迷。",
        7: "驚慌逃跑：就算取走唯一的車輛，留下其他人，也要用盡一切辦法逃離現場。",
        8: "歇斯底里或情緒失控：因為大笑，大哭或驚慌尖叫等原因而失去行動能力。",
        9: "恐懼：增加一個新的恐懼症，可以骰1D100參考「CoC7版恐懼症狀表」或由KP選擇症狀。即使恐懼的對象不在附近，也會覺得它是存在的。",
        10: "狂熱：增加一個新的狂躁症，可以骰1D100參考「CoC7版狂躁症狀表」或由KP選擇症狀。接下來會沉溺於這個狂躁症狀中。"
    },
    "SU": {
        1: "失憶：不能回憶自己的過去和對自己的身份模糊不清，而且對所在地點沒有印象。記憶會隨時間恢復。",
        2: "被搶：當恢復理智後，發現身上沒有受傷，但有價值的事物全部消失。假如身上帶著「珍視的東西」，骰一個幸運檢定，失敗則同樣被搶。",
        3: "遍體鱗傷：當恢復理智後，發現身上有傷痕，HP減少到狂氣發作前的一半，但這些不會造成重傷。身上物品沒有被搶。如何受傷由KP去決定。",
        4: "暴力沖動：陷入強烈的暴力和破壞沖動之中。當恢復理智後，可能會記得自己這段時間的所作所為。由ＫＰ決定因為狂氣發作所作出的破壞，無論是破壞了什麼物件，傷害甚至殺死什麼人。",
        5: "信仰/信念：選擇背景條目「信仰/信念」其中一樣，極端、瘋狂而又情緒化去表達之。例如：一個有宗教信仰的角色在地鐵裡大聲報福音。",
        6: "重要人士：採取一切手段去接近背景條目中的「重要人士」，並且做出與該條目內容相關聯的行動。例如：如果該重要人士是愛人，則表現出極端的保護慾。",
        7: "被擄：當恢復理智後發現自己在一個封閉的地方，例如地牢、棺材或者是某個人的地下室裡。此時可能需要進行一個新的理智檢定。",
        8: "幻覺：經歷了生動且恐怖的幻覺。當恢復理智後，會覺得現實世界可能也是虛假的。",
        9: "恐懼：增加一個新的恐懼症，參考「CoC7版恐懼症狀表」或由KP選擇症狀。",
        10: "狂熱：增加一個新的狂躁症參考，「CoC7版狂躁症狀表」或由KP選擇症狀。"
    }
}

bot = MyBot()

@bot.event
async def on_ready():
    # 使用你原本帥氣的自定義狀態
    custom_status = discord.CustomActivity(name="🔥 前往地獄。")
    await bot.change_presence(status=discord.Status.online, activity=custom_status)
    print(f'Mephistopheles  已上線')

# --- 5. 訊息指令整合 ---
@bot.event
async def on_message(message):
    if message.author == bot.user: return
    
    content = message.content.strip()
    low_content = content.lower()

    # [1] 隨機選擇
    if content.startswith("隨機 "):
        opts = content.split()[1:]
        if opts:
            await message.channel.send(f"{message.author.mention} 隨機選擇：**{random.choice(opts)}**")
        return

# [2] CC 指令 (支援判定難度與大成功/大失敗)
    if low_content.startswith("!cc") or low_content.startswith("cc"):
        match = re.match(r'^!?cc(n)?(\d*)?\s+([\d,]+)(.*)', low_content)
        if match:
            is_penalty = match.group(1) == 'n' 
            num_str = match.group(2)
            bonus_dice = int(num_str) if num_str else (1 if is_penalty else 0)
            if is_penalty: bonus_dice = -bonus_dice
            
            targets = [int(x.strip()) for x in match.group(3).split(',')]
            event = match.group(4).strip()
            
            # 處理獎懲骰邏輯
            base_roll = random.randint(1, 100)
            rolls = [base_roll]
            for _ in range(abs(bonus_dice)):
                tens = random.randint(0, 9) * 10
                new_roll = tens + (base_roll % 10)
                if new_roll == 0: new_roll = 100
                rolls.append(new_roll)
            
            final_res = min(rolls) if bonus_dice >= 0 else max(rolls)
            roll_str = f"[{' / '.join(map(str, rolls))}] → {final_res}" if len(rolls) > 1 else str(final_res)
            
            output = [f"{message.author.mention} {event}"]
            if len(targets) > 1: output.append("### 聯合檢定")
            if bonus_dice != 0:
                dice_label = "獎勵骰" if bonus_dice > 0 else "懲罰骰"
                output.append(f"({dice_label}：{abs(bonus_dice)})")

            # --- 判定成功等級的核心邏輯 ---
            for tar in targets:
                # 預設狀態
                status = "失敗"
                
                # 1. 判斷大成功與大失敗
                if final_res <= 5: 
                    status = "★大成功！"
                elif final_res >= 96: 
                    status = "✘大失敗！"
                # 2. 判斷成功等級
                elif final_res <= (tar // 5): 
                    status = "極限成功"
                elif final_res <= (tar // 2): 
                    status = "困難成功"
                elif final_res <= tar: 
                    status = "普通成功"
                
                output.append(f"1D100 ≤ {tar} | 結果：{roll_str} → **{status}**")

            await message.channel.send("\n".join(output))
            return

# [3] 複數重複擲骰 (!r 次數 算式)
    if low_content.startswith("!r "):
        cmd = content[3:].strip()
        groups = cmd.split(',')
        view = DiceView()
        
        # 按鈕顏色清單
        button_styles = [
            discord.ButtonStyle.primary,   # 藍
            discord.ButtonStyle.success,   # 綠
            discord.ButtonStyle.danger,    # 紅
            discord.ButtonStyle.secondary  # 灰
        ]
        
        for idx, g in enumerate(groups):
            # 支援更寬鬆的空格匹配
            m = re.match(r'(\d+)\s+([\dd\+\-\(\)\*\/ ]+)', g.strip())
            if m:
                times, expr = int(m.group(1)), m.group(2).strip()
                res_details = [f"{times}次 {expr} 詳細結果："]
                
                for i in range(1, times + 1):
                    val, det = evaluate_expr(expr)
                    # 達成要求格式：#1 2[1+1] = 4
                    res_details.append(f"#{i} {det} = {val}")
                
                # 顏色輪替與按鈕建立
                current_style = button_styles[idx % len(button_styles)]
                view.add_item(DiceButton(label=f"{times}次 {expr}", details="\n".join(res_details), style=current_style))
        
        if len(view.children) > 0:
            await message.channel.send(f"{message.author.mention} 複數擲骰完成：", view=view)
        return

    # [4] 直接顯示版複數擲骰 (格式: 3 1d100)
    sep_match = re.match(r'^(\d+)\s+([\dd\+\-\(\)\*\/]+)(.*)$', low_content)
    if sep_match:
        times, expr, evt = int(sep_match.group(1)), sep_match.group(2), sep_match.group(3).strip()
        if 'd' in expr: # 確保算式中包含骰子
            res_list = [f"{times}次分開結果 ({expr}) {evt}："]
            for i in range(1, times + 1):
                val, det = evaluate_expr(expr)
                res_list.append(f"{i}# {val} ({det})")
            await message.channel.send(f"{message.author.mention}\n\n" + "\n".join(res_list) + "\n")
            return

    # [5] 一般單次擲骰
    dice_start_match = re.match(r'^([\dd\+\-\(\)\*\/]+)(.*)$', low_content)
    if dice_start_match:
        expr = dice_start_match.group(1)
        if 'd' in expr:
            evt = content[len(expr):].strip()
            val, det = evaluate_expr(expr)
            await message.channel.send(f"{message.author.mention} {evt}\n\n{expr}\n結果：{det}\n總和：{val}\n")
            return

    await bot.process_commands(message)

    # [6] CCRT 與 CCSU 
    if low_content == "!ccrt":
        res = random.randint(1, 10)
        duration = random.randint(1, 10) # 額外擲 1D10 輪
        await message.reply(f"**狂氣發作 - 即時症狀**\n持續輪數：{duration} 輪\n{MADNESS_DATA['RT'][res]}\n")
        return

    if low_content == "!ccsu":
        res = random.randint(1, 10)
        duration = random.randint(1, 10) # 額外擲 1D10 小時
        await message.reply(f"**狂氣發作 - 總結症狀**\n持續時間：{duration} 小時\n{MADNESS_DATA['SU'][res]}\n")
        return

# [7] SC 理智檢定修正版 (格式: !sc 成功率 基礎值/失敗額外骰子)
    if low_content.startswith("!sc"):
        # 正則解析：!sc 成功率 基礎/失敗骰
        # 例如：!sc 50 1/1d3
        match = re.match(r'^!sc\s+(\d+)\s+([\dd\+\-]+)/([\dd\+\-]+)(.*)', low_content)
        if match:
            target = int(match.group(1))
            base_val_expr = match.group(2)    # 成功的基礎扣除點數
            fail_extra_expr = match.group(3)  # 失敗時額外加骰的算式
            event = match.group(4).strip()
            
            # 1. 進行理智檢定 (1D100)
            res = random.randint(1, 100)
            is_success = res <= target
            
            status = "成功" if is_success else "失敗"
            if res <= 5: status = "★大成功！"
            elif res >= 96: status = "✘大失敗！"
            
            # 2. 計算損害
            # 先算出基礎值 (base)
            base_dmg, base_det = evaluate_expr(base_val_expr)
            
            if is_success:
                # 成功：只扣基礎值
                final_val = base_dmg
                final_det = base_det
            else:
                # 失敗：基礎值 + 失敗額外骰子
                extra_dmg, extra_det = evaluate_expr(fail_extra_expr)
                final_val = base_dmg + extra_dmg
                final_det = f"{base_det} + {extra_det}"
            
            # 3. 輸出訊息
            output = [
                f"{message.author.mention} **理智檢定 (Sanity Check)** {event}",
                f"1D100 ≤ {target} | 結果：{res} → **{status}**",
                f"理智損害：{final_det} = **{final_val}** 點"
            ]
            
            await message.channel.send("\n".join(output))
            return

# [8] CG 成長檢定修正版 (支援多重目標)
    if low_content.startswith("!cg"):
        # 取得指令後的內容
        cmd_text = content[3:].strip()
        # 使用正則表達式找出所有的「數字」與其後的「文字說明」
        # 匹配格式：數字 + (選填的非數字名稱)
        matches = re.findall(r'(\d+)\s*([^\d,]*)', cmd_text)
        
        if not matches:
            await message.reply("格式錯誤！請輸入如 `!cg 50 騎乘 60 鬥毆` 或 `!cg 50 60`")
            return

        output = [f"{message.author.mention} **技能成長檢定**"]
        
        for val_str, name in matches:
            current_val = int(val_str)
            skill_name = name.strip() if name.strip() else "未知技能"
            
            # 1. 進行成長判定 (1D100)
            res = random.randint(1, 100)
            # 規則：結果 > 技能值 或 結果 > 95
            is_growth = res > current_val or res > 95
            
            result_text = f"● **{skill_name}** ({current_val}) → 1D100={res}"
            
            if is_growth:
                # 2. 成長成功，擲 1D10
                growth_val = random.randint(1, 10)
                new_val = current_val + growth_val
                output.append(f"{result_text} | **成功！** (+{growth_val}) → **{new_val}**")
            else:
                output.append(f"{result_text} | 失敗")
        
        await message.channel.send("\n".join(output))
        return

# --- 成功結果邏輯 ---

def get_coc_status(res: int, target: int):
    """判定 CoC 成功等級"""
    if res <= 5: return "★大成功！"
    if res >= 96: return "✘大失敗！"
    if res <= (target // 5): return "極限成功"
    if res <= (target // 2): return "困難成功"
    if res <= target: return "普通成功"
    return "失敗"

def roll_dice(dice_str: str) -> int:
    """解析 1d6, 1d3+1 等骰子格式"""
    import re
    try:
        # 處理純數字
        if dice_str.isdigit(): return int(dice_str)
        # 處理 NdM+X 格式
        match = re.match(r"(\d+)d(\d+)(?:\+(\d+))?", dice_str.lower())
        if match:
            n, m, bonus = match.groups()
            total = sum(random.randint(1, int(m)) for _ in range(int(n)))
            if bonus: total += int(bonus)
            return total
        return 0
    except:
        return 0

# --- 6. 斜線指令整合 ---

@bot.tree.command(name="暗骰", description="暗骰功能：可選擇僅自己看見，或額外轉發給指定 GM")
@app_commands.describe(
    expr="骰子算式或 CC 目標 (例如: 1d100, 2d6+5, 或技能值 50)",
    target_gm="選擇要接收結果的 GM (選填)",
    is_cc="這是否為 CC 檢定？(預設為否)",
    event="檢定項目名稱 (選填)"
)
async def secret_dice_slash(
    interaction: discord.Interaction, 
    expr: str, 
    is_cc: bool = False,
    target_gm: discord.Member = None,
    event: str = "暗骰檢定"
):
    # 確保傳入 evaluate_expr 的字串是乾淨且小寫的
    clean_expr = expr.strip().lower()

    # 1. 執行運算邏輯
    if is_cc:
        try:
            target = int(clean_expr)
            res = random.randint(1, 100)
            # 使用您定義好的 get_coc_status
            status = get_coc_status(res, target)
            final_text = (
                f"**{event} (CC 暗骰)**\n"
                f"判定：1D100 ≤ {target}\n"
                f"結果：{res} → **{status}**"
            )
        except ValueError:
            await interaction.response.send_message("CC 檢定請輸入純數字（目標值）", ephemeral=True)
            return
    else:
        # 正確呼叫 evaluate_expr 並獲取回傳值
        val, det = evaluate_expr(clean_expr)
        
        # 修正：確保顯示的算式與過程清晰
        final_text = (
            f"**{event} (暗骰)**\n"
            f"算式：{expr}\n"
            f"過程：{det}\n"
            f"結果：**{val}**"
        )

    # 2. 建立發送給 GM 的 View (參考 SecretDiceView)
    view = SecretDiceView(target_gm, final_text) if target_gm else None
    
    # 3. 發送 ephemeral 訊息 (只有發起者看得到)
    # 加上 Emoji 提示訊息狀態
    await interaction.response.send_message(
        f"**這是您的暗骰結果：**\n\n{final_text}" + 
        (f"\n\n按下方按鈕將此結果轉發給 GM: **{target_gm.display_name}**" if target_gm else ""),
        view=view,
        ephemeral=True
    )

@bot.tree.command(name="隨機", description="隨機選擇選項")
async def choose(interaction: discord.Interaction, options: str):
    opt_list = options.split()
    if not opt_list:
        await interaction.response.send_message("請提供選項！", ephemeral=True)
        return
    await interaction.response.send_message(f"{interaction.user.mention}\n選項：{'/'.join(opt_list)}\n→ **{random.choice(opt_list)}**")

@bot.tree.command(name="cc檢定", description="屬性或技能檢定 (1D100)")
@app_commands.describe(target="成功率 (技能值)", event="檢定項目名稱")
async def cc_slash(interaction: discord.Interaction, target: int, event: str = "檢定"):
    res = random.randint(1, 100)
    status = get_coc_status(res, target)
    
    await interaction.response.send_message(
        f"{interaction.user.mention} **{event}**\n"
        f"判定：1D100 ≤ {target}\n"
        f"結果：{res} → **{status}**"
    )

@bot.tree.command(name="一般擲骰", description="支援+-和*語法")
async def r_slash(interaction: discord.Interaction, dice: str, event: str = ""):
    val, det = evaluate_expr(dice.lower())
    await interaction.response.send_message(f"{interaction.user.mention} {event}\n{dice}\n結果：{det}\n→ {val}\n")

@bot.tree.command(name="ccrt", description="狂氣發作 - 即時症狀 (持續 1D10 輪)")
async def ccrt_slash(interaction: discord.Interaction):
    res = random.randint(1, 10)
    duration = random.randint(1, 10) # 擲出 1D10 輪
    msg = (
        f"{interaction.user.mention} **狂氣發作 - 即時症狀**\n"
        f"**持續時間**：{duration} 輪\n"
        f"{MADNESS_DATA['RT'][res]}\n"
    )
    await interaction.response.send_message(msg)

@bot.tree.command(name="ccsu", description="狂氣發作 - 總結症狀 (持續 1D10 小時)")
async def ccsu_slash(interaction: discord.Interaction):
    res = random.randint(1, 10)
    duration = random.randint(1, 10) # 擲出 1D10 小時
    msg = (
        f"{interaction.user.mention} **狂氣發作 - 總結症狀**\n"
        f"**持續時間**：{duration} 小時\n"
        f"{MADNESS_DATA['SU'][res]}\n"
    )
    await interaction.response.send_message(msg)

@bot.tree.command(name="理智檢定", description="理智檢定 (失敗時會扣除：基礎值 + 額外骰子)")
@app_commands.describe(
    target="目前的理智值", 
    base="成功的扣除點數 (如: 0, 1)", 
    extra="失敗時「額外」加骰的算式 (如: 1d6)",
    event="觸發原因"
)
async def sc_slash(interaction: discord.Interaction, target: int, base: str, extra: str, event: str = "理智檢定"):
    res = random.randint(1, 100)
    is_success = res <= target
    status = get_coc_status(res, target)
    
    # 轉換基礎值為整數
    base_val = roll_dice(base) 
    
    if is_success:
        # 成功：只扣基礎值
        loss_val = base_val
        loss_formula = base
    else:
        # 失敗：基礎值 + 額外骰子
        extra_val = roll_dice(extra)
        loss_val = base_val + extra_val
        loss_formula = f"{base} + {extra}"

    msg = [
        f"{interaction.user.mention} **{event}**",
        f"判定：1D100 ≤ {target} | 結果：{res} → **{status}**",
        f"理智減少：**{loss_val}** 點 (公式：{loss_formula})"
    ]
    await interaction.response.send_message("\n".join(msg))

@bot.tree.command(name="技能成長檢定", description="結果 > 技能值則成長)")
@app_commands.describe(skill_val="目前的技能值", skill_name="技能名稱")
async def cg_slash(interaction: discord.Interaction, skill_val: int, skill_name: str = "技能"):
    res = random.randint(1, 100)
    # 成長條件：1D100 大於技能值，或者結果 > 95
    is_growth = res > skill_val or res > 95
    
    output = [
        f"{interaction.user.mention} **成長檢定：{skill_name}**",
        f"判定：1D100 > {skill_val} | 結果：{res}"
    ]
    
    if is_growth:
        growth_add = random.randint(1, 10)
        output.append(f"→ **成長成功！** 增加 1D10 ({growth_add}) 點")
        output.append(f"新數值：{skill_val} + {growth_add} = **{skill_val + growth_add}**")
    else:
        output.append("→ **成長失敗**，數值保持不變。")
    
    await interaction.response.send_message("\n".join(output))

# --- 7. 系統匣控制邏輯 (保留你原本的功能) ---
def quit_window(icon, item):
    icon.stop()
    os._exit(0)

def setup_tray():
    icon_path = resource_path("icon.png")
    try:
        image = Image.open(icon_path)
    except:
        image = Image.new('RGB', (64, 64), (255, 0, 0))
    menu = pystray.Menu(pystray.MenuItem('關閉 Mephistopheles', quit_window))
    icon = pystray.Icon("Mephistopheles", image, "Mephistopheles 擲骰機器人", menu)
    icon.run()

# --- 8. 啟動入口 ---
if __name__ == "__main__":
    tray_thread = threading.Thread(target=setup_tray, daemon=True)
    tray_thread.start()
    
bot.run('DISCORD_BOT_TOKEN')
