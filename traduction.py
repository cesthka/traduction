"""
Discord Translation Bot
------------------------
Command prefix: *

Commands:
  *translate <text>        (alias *tr)   -> auto-detects the language, translates to English
  *translate <code> <text>               -> translate to a given language (e.g. fr, es, tr)
  *languages               (alias *langs)-> list available language codes
  *auto                                  -> toggle auto-translation in the current channel
  *help                                  -> show help

  🐕 Leash system (owner / whitelist only):
  *leash <@member|id>      (alias *dog)  -> leash a member (or unleash if already leashed)
  *dogs                                  -> show your leashed members
  *wl add|remove|list [@member]          -> manage the whitelist (owner only)

Bonus:
  React with a country flag on a message -> translates it into that language.

The Turkish input is cleaned up before translation (slang, missing accents,
repeated letters, upper/lowercase) for much better quality.
"""

import os
import json
import re
import discord
from discord.ext import commands, tasks
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

# Load a .env file if present (handy locally).
# If python-dotenv isn't installed, just skip this step.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────

TOKEN = os.environ.get("DISCORD_TOKEN")
PREFIX = "*"

# Per-user translation: everyone picks their own language with *set.
# Default target only used as a fallback in the engine; *translate uses each
# user's chosen language instead.
DEFAULT_TARGET = "en"

# Auto-translation always targets French (kept on purpose).
AUTO_TARGET = "fr"

# How often the reminder message is posted in auto channels.
REMINDER_MINUTES = 15

# Hardcoded bot owner ID: only this user can run owner-only commands.
OWNER_ID = 142365250803466240

# Channels where auto-translation is active (managed via the owner-only *auto).
# Loaded from the data file at startup so it survives restarts.
auto_channels: set[int] = set()

# ─── Leash (dog) system ───────────────────────────────────────
DOG_EMOJI = "🐕"
DATA_FILE = "leash_data.json"  # created automatically next to bot.py


def load_data() -> dict:
    """Load leashes, whitelist, per-user languages and auto channels."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("leashes", {})        # { "guild_id:user_id": {...} }
    d.setdefault("wl", [])             # [user_id, ...]
    d.setdefault("user_lang", {})      # { "user_id": "lang_code" }
    d.setdefault("auto_channels", [])  # [channel_id, ...]
    d.setdefault("reminder_channel", None)  # channel_id for reminder messages
    d.setdefault("allowed_channels", [])    # channels where commands are allowed
    return d


def save_data() -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


data = load_data()
auto_channels = set(data["auto_channels"])  # in-memory mirror, kept in sync


def get_user_lang(user_id: int) -> str | None:
    """Return the language a user has set, or None if they haven't set one."""
    return data["user_lang"].get(str(user_id))


def set_user_lang(user_id: int, code: str) -> None:
    data["user_lang"][str(user_id)] = code
    save_data()


def leash_key(guild_id: int, user_id: int) -> str:
    return f"{guild_id}:{user_id}"


def format_nick(base_name: str, master_name: str) -> str:
    """Build '<name> (🐕 of <master>)' within Discord's 32-char limit."""
    suffix = f" ({DOG_EMOJI} of {master_name})"
    room = 32 - len(suffix)
    if room < 1:
        # Master name too long: keep just the truncated suffix
        return suffix.strip()[:32]
    return base_name[:room] + suffix


# ──────────────────────────────────────────────────────────────
#  TRANSLATION ENGINE
# ──────────────────────────────────────────────────────────────

# Optional DeepL engine: if the DEEPL_API_KEY env var is set, it is used first
# (better quality), with an automatic fallback to Google.
DEEPL_KEY = os.environ.get("DEEPL_API_KEY")

# Known languages: code -> (English name, flag)
LANGUAGES = {
    "en": ("English", "🇬🇧"),
    "fr": ("French", "🇫🇷"),
    "tr": ("Turkish", "🇹🇷"),
    "es": ("Spanish", "🇪🇸"),
    "de": ("German", "🇩🇪"),
    "it": ("Italian", "🇮🇹"),
    "pt": ("Portuguese", "🇵🇹"),
    "nl": ("Dutch", "🇳🇱"),
    "ru": ("Russian", "🇷🇺"),
    "ar": ("Arabic", "🇸🇦"),
    "ja": ("Japanese", "🇯🇵"),
    "ko": ("Korean", "🇰🇷"),
    "zh-CN": ("Chinese", "🇨🇳"),
    "pl": ("Polish", "🇵🇱"),
    "ro": ("Romanian", "🇷🇴"),
    "uk": ("Ukrainian", "🇺🇦"),
    "el": ("Greek", "🇬🇷"),
    "sv": ("Swedish", "🇸🇪"),
    "hi": ("Hindi", "🇮🇳"),
}

# Reacting with one of these flags translates the message into that language.
FLAG_TO_LANG = {
    "🇬🇧": "en", "🇺🇸": "en", "🇫🇷": "fr", "🇹🇷": "tr", "🇪🇸": "es",
    "🇩🇪": "de", "🇮🇹": "it", "🇵🇹": "pt", "🇧🇷": "pt", "🇳🇱": "nl",
    "🇷🇺": "ru", "🇸🇦": "ar", "🇯🇵": "ja", "🇰🇷": "ko", "🇨🇳": "zh-CN",
    "🇵🇱": "pl", "🇷🇴": "ro", "🇺🇦": "uk", "🇬🇷": "el", "🇸🇪": "sv", "🇮🇳": "hi",
}

CHAR_LIMIT = 4500  # stays under the engines' ~5000-char cap


def language_info(code: str | None) -> tuple[str, str]:
    """Return (name, flag) for a language code, normalized."""
    if not code:
        return ("Unknown", "🌐")
    code = code.lower()
    if code in ("zh", "zh-cn"):
        code = "zh-CN"
    for k, v in LANGUAGES.items():
        if k.lower() == code.lower():
            return v
    return (code, "🌐")


def detect_language(text: str) -> str | None:
    try:
        return detect(text)
    except LangDetectException:
        return None


def _chunk(text: str, size: int = CHAR_LIMIT) -> list[str]:
    """Split long text into chunks under the limit, without cutting words."""
    if len(text) <= size:
        return [text]
    chunks, current = [], ""
    for word in text.split(" "):
        if len(current) + len(word) + 1 > size:
            if current:
                chunks.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        chunks.append(current)
    return chunks


def _engine(text: str, target: str, source: str = "auto") -> str:
    """Translate one chunk: DeepL if available (Google fallback on failure)."""
    if DEEPL_KEY:
        try:
            from deep_translator import DeeplTranslator
            return DeeplTranslator(
                api_key=DEEPL_KEY, source=source, target=target, use_free_api=True
            ).translate(text)
        except Exception:
            pass  # silent fallback to Google
    return GoogleTranslator(source=source, target=target).translate(text)


# ══════════════════════════════════════════════════════════════
#  TURKISH PRE-PROCESSING  (for much better TR -> XX translation)
# ══════════════════════════════════════════════════════════════
# Chat Turkish is often abbreviated (slm, nbr, tmm), written without accents
# (cok instead of çok), or with repeated letters (çoook). Translation engines
# handle this poorly, so we rewrite the text into proper Turkish BEFORE
# translating, which greatly improves comprehension.

# 1) Slang / abbreviations -> proper Turkish
SLANG_TR = {
    "slm": "selam", "mrb": "merhaba", "mrhb": "merhaba",
    "nbr": "ne haber", "naber": "ne haber", "nrb": "ne haber", "napıyon": "ne yapıyorsun",
    "napıyosun": "ne yapıyorsun", "napıyorsun": "ne yapıyorsun", "naptın": "ne yaptın",
    "napcaz": "ne yapacağız", "noldu": "ne oldu", "noluyo": "ne oluyor",
    "sa": "selamün aleyküm", "as": "aleyküm selam",
    "tmm": "tamam", "tm": "tamam", "tamamdır": "tamam",
    "tşk": "teşekkürler", "tsk": "teşekkürler", "tskler": "teşekkürler", "tşkler": "teşekkürler",
    "eyw": "eyvallah", "eyv": "eyvallah",
    "knk": "kanka", "kanki": "kanka", "abicim": "abi",
    "bb": "bay bay", "gs": "görüşürüz", "grsrz": "görüşürüz", "grş": "görüşürüz",
    "cnm": "canım", "askm": "aşkım", "slmlr": "selamlar",
    "ewt": "evet", "evt": "evet", "hyr": "hayır", "yh": "ya", "yha": "ya",
    "bi": "bir", "bişi": "bir şey", "bişey": "bir şey", "bişeyler": "bir şeyler",
    "valla": "vallahi", "vallaha": "vallahi", "vallahi": "vallahi",
    "niye": "neden", "knkam": "kankam",
    "msj": "mesaj", "selamun": "selamün",
}

# 2) Accent-less Turkish -> accented Turkish (unambiguous forms only)
ACCENTS_TR = {
    "nasilsin": "nasılsın", "nasilsiniz": "nasılsınız", "nasil": "nasıl",
    "gunaydin": "günaydın", "iyiyim": "iyiyim", "tesekkur": "teşekkür",
    "tesekkurler": "teşekkürler", "tessekur": "teşekkür", "cok": "çok",
    "guzel": "güzel", "degil": "değil", "icin": "için", "simdi": "şimdi",
    "sey": "şey", "hersey": "her şey", "herseyi": "her şeyi", "herseye": "her şeye",
    "oyle": "öyle", "boyle": "böyle", "soyle": "söyle", "uzgunum": "üzgünüm",
    "gormek": "görmek", "goruyorum": "görüyorum", "gorusuruz": "görüşürüz",
    "dogru": "doğru", "yanlis": "yanlış", "gunes": "güneş", "kardes": "kardeş",
    "calisiyorum": "çalışıyorum", "calismak": "çalışmak", "calisma": "çalışma",
    "gidecegim": "gideceğim", "gelecegim": "geleceğim", "yapacagim": "yapacağım",
    "anladim": "anladım", "anlamadim": "anlamadım", "yapiyorum": "yapıyorum",
    "ozur": "özür", "lutfen": "lütfen", "hayir": "hayır", "dusunuyorum": "düşünüyorum",
    "gercekten": "gerçekten", "turkce": "türkçe", "fransizca": "fransızca",
    "universite": "üniversite", "ogrenci": "öğrenci", "ogretmen": "öğretmen",
    "sarki": "şarkı", "mumkun": "mümkün", "gunah": "günah", "mujde": "müjde",
    "cocuk": "çocuk", "gun": "gün", "bugun": "bugün", "dun": "dün",
    "yarin": "yarın", "sukur": "şükür", "soyluyorum": "söylüyorum",
    "biliyom": "biliyorum", "gidiyom": "gidiyorum", "geliyom": "geliyorum",
}

# Distinctly Turkish words: their presence signals Turkish (even without accents)
TURKISH_HINTS = {
    "bir", "bu", "ben", "sen", "biz", "için", "icin", "çok", "cok", "değil", "degil",
    "var", "yok", "evet", "hayır", "hayir", "nasıl", "nasil", "nasılsın", "nasilsin",
    "selam", "merhaba", "teşekkürler", "tesekkurler", "kanka", "abi", "tamam",
    "naber", "valla", "günaydın", "gunaydin", "güzel", "guzel", "değilim",
    "ve", "ama", "şey", "sey", "çünkü", "cunku", "şimdi", "simdi", "kardeş", "kardes",
}

# Turkish-distinct characters, lower AND upper case. We deliberately exclude
# i / I (ambiguous) and ç/ö/ü (also used in French).
TURKISH_CHARS = set("ışğİŞĞ")


def _collapse_repeats(word: str) -> str:
    """çoook -> çok, yaaa -> ya (collapse 3+ identical letters to 1)."""
    return re.sub(r"(.)\1{2,}", r"\1", word)


def _lower_forms(word: str) -> tuple[str, ...]:
    """Return the possible lowercase forms of a word.

    In Turkish, uppercase 'I' becomes 'ı' (dotless), whereas elsewhere it is 'i'.
    We test BOTH versions so detection works in UPPERCASE.
    e.g. 'NASILSIN' -> ('nasilsin', 'nasılsın')
    """
    standard = word.lower()                                  # I -> i
    turkish = word.replace("İ", "i").replace("I", "ı").lower()  # I -> ı
    return (standard,) if standard == turkish else (standard, turkish)


def looks_turkish(text: str) -> bool:
    """True if the text is likely Turkish (even in uppercase / without accents)."""
    if any(c in TURKISH_CHARS for c in text):
        return True
    if detect_language(text) == "tr":
        return True
    words = re.findall(r"\w+", text, re.UNICODE)
    hits = 0
    for w in words:
        if any(
            f in TURKISH_HINTS or f in SLANG_TR or f in ACCENTS_TR
            for f in _lower_forms(w)
        ):
            hits += 1
    # 1 hint is enough for a short message, 2 for a longer one
    return hits >= (1 if len(words) <= 3 else 2)


def preprocess_turkish(text: str) -> str:
    """Rewrite chat Turkish into proper Turkish before translation (uppercase included)."""
    tokens = re.findall(r"\w+|[^\w\s]+|\s+", text, re.UNICODE)
    out = []
    for tok in tokens:
        if tok.isspace() or not re.match(r"\w", tok, re.UNICODE):
            out.append(tok)
            continue
        base = _collapse_repeats(tok)
        replacement = None
        for form in _lower_forms(base):
            if form in SLANG_TR:
                replacement = SLANG_TR[form]
                break
            if form in ACCENTS_TR:
                replacement = ACCENTS_TR[form]
                break
        out.append(replacement if replacement is not None else base)
    return "".join(out)


def translate_text(text: str, target: str = DEFAULT_TARGET) -> str:
    """Translate text to `target`. Turkish gets dedicated pre-processing plus a
    forced source language for much higher quality."""
    source = "auto"
    to_translate = text
    if target != "tr" and looks_turkish(text):
        to_translate = preprocess_turkish(text)
        source = "tr"
    parts = [_engine(c, target, source) for c in _chunk(to_translate)]
    return " ".join(p for p in parts if p)


# ──────────────────────────────────────────────────────────────
#  BOT
# ──────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # required for the leash system (nicknames, joins)

# Remove the default help command so we can use our own.
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id: {bot.user.id})")
    print(f"   Prefix: {PREFIX}")
    if not reminder_loop.is_running():
        reminder_loop.start()


# ─── Periodic reminder (rotating messages, every REMINDER_MINUTES) ─────
REMINDER_MESSAGES = [
    f"🌐 **Don't forget to set your language!** Run `{PREFIX}set` to pick it, then "
    f"use `{PREFIX}translate <text>` to translate anything into your own language.",

    f"🌐 **Can't understand a message?** React to it with your country's flag, or use "
    f"`{PREFIX}translate <text>`. First time? Set your language with `{PREFIX}set`.",

    f"🌐 **Tip:** with `{PREFIX}set` you choose your language once, and every "
    f"`{PREFIX}translate` after that comes back in that language. Give it a try!",
]
_reminder_index = 0


@tasks.loop(minutes=REMINDER_MINUTES)
async def reminder_loop():
    global _reminder_index
    # Prefer the dedicated reminder channel; otherwise fall back to auto channels.
    reminder_channel = data.get("reminder_channel")
    if reminder_channel:
        targets = [reminder_channel]
    elif auto_channels:
        targets = list(auto_channels)
    else:
        return  # nowhere to post

    message = REMINDER_MESSAGES[_reminder_index % len(REMINDER_MESSAGES)]
    _reminder_index += 1
    for channel_id in targets:
        channel = bot.get_channel(channel_id)
        if channel is not None:
            try:
                await channel.send(message)
            except Exception as e:
                print(f"Reminder error: {e}")


@reminder_loop.before_loop
async def _before_reminder():
    await bot.wait_until_ready()


# ─── Check: owner-only ─────────────────────────────────────────
def is_owner():
    """Decorator: add @is_owner() above a command to restrict it to the owner."""
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)


# ─── Global check: command channels ───────────────────────────
class WrongChannel(commands.CheckFailure):
    """Raised when a command is used in a non-allowed channel."""


# Commands allowed anywhere (translate works everywhere; the allow-management
# commands must work anywhere so the owner can never get locked out).
EXEMPT_COMMANDS = {"translate", "allow", "unallow", "allows"}


@bot.check
async def channel_allowed(ctx: commands.Context) -> bool:
    if ctx.guild is None:  # never restrict DMs
        return True
    if ctx.command and ctx.command.name in EXEMPT_COMMANDS:
        return True
    # The owner and whitelisted users can use commands anywhere they want.
    if ctx.author.id == OWNER_ID or ctx.author.id in data["wl"]:
        return True
    allowed = data.get("allowed_channels", [])
    if not allowed:  # nothing configured yet -> no restriction
        return True
    if ctx.channel.id in allowed:
        return True
    raise WrongChannel()


# ─── UI: language picker for *set ─────────────────────────────
class LanguageSelect(discord.ui.Select):
    def __init__(self, author_id: int):
        self.author_id = author_id
        options = [
            discord.SelectOption(label=name, value=code, emoji=flag)
            for code, (name, flag) in LANGUAGES.items()
        ]
        super().__init__(
            placeholder="Choose your language…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        # Only the person who ran *set may use their own menu.
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "⛔ This menu isn't for you. Run `*set` to choose your own language.",
                ephemeral=True,
            )
            return
        code = self.values[0]
        set_user_lang(self.author_id, code)
        name, flag = language_info(code)
        embed = discord.Embed(
            title="✅ Language set",
            description=(
                f"Your language is now {flag} **{name}**.\n"
                f"Use `{PREFIX}translate <text>` and translations will come to you "
                f"in {name}."
            ),
            color=0x3BA55D,
        )
        # Disable the menu after a choice is made.
        self.disabled = True
        await interaction.response.edit_message(embed=embed, view=self.view)


class SetLanguageView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.add_item(LanguageSelect(author_id))


# ─── Command: set (choose your language) ──────────────────────
@bot.command(name="set")
async def set_lang(ctx: commands.Context):
    """Open a menu to pick your personal translation language."""
    current = get_user_lang(ctx.author.id)
    if current:
        name, flag = language_info(current)
        desc = f"Your current language is {flag} **{name}**.\nPick a new one below:"
    else:
        desc = "Pick the language you want your translations in:"
    embed = discord.Embed(title="🌐 Set your language", description=desc, color=0x3B88C3)
    await ctx.reply(embed=embed, view=SetLanguageView(ctx.author.id), mention_author=False)


# ─── Command: translate ───────────────────────────────────────
@bot.command(name="translate", aliases=["tr", "t"])
async def translate(ctx: commands.Context, *, content: str = None):
    """Translate text into the language YOU set with *set.

    Usage:
      • *translate <text>                  -> translate into your language
      • (replying to a message) *translate -> translate that message into your language
    """
    # Everyone can use this, but they must have set a language first.
    target = get_user_lang(ctx.author.id)
    if not target:
        await ctx.reply(
            f"🌐 You need to set your language first. Run `{PREFIX}set` and pick "
            "your language, then you can translate anything."
        )
        return

    text = content

    # Reply case: translate the referenced message.
    if not text and ctx.message.reference is not None:
        ref = ctx.message.reference
        target_msg = ref.resolved
        if target_msg is None or isinstance(target_msg, discord.DeletedReferencedMessage):
            try:
                target_msg = await ctx.channel.fetch_message(ref.message_id)
            except Exception:
                target_msg = None
        if target_msg and target_msg.content:
            text = target_msg.content

    if not text:
        await ctx.reply(
            f"Usage: `{PREFIX}translate <text>`, or reply to a message with "
            f"`{PREFIX}translate`."
        )
        return

    try:
        async with ctx.typing():
            translation = translate_text(text, target)
        src_code = detect_language(text)
        src_name, src_flag = language_info(src_code)
        dst_name, dst_flag = language_info(target)

        embed = discord.Embed(color=0x3B88C3)
        embed.add_field(name=f"{src_flag} {src_name}", value=text[:1024], inline=False)
        embed.add_field(name=f"{dst_flag} {dst_name}", value=translation[:1024], inline=False)
        await ctx.reply(embed=embed, mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Translation error: {e}")


# ─── Command: languages ───────────────────────────────────────
@bot.command(name="languages", aliases=["langs"])
async def languages(ctx: commands.Context):
    """Show the available languages (the same list as in *set)."""
    lines = [f"{flag} `{code}` — {name}" for code, (name, flag) in LANGUAGES.items()]
    embed = discord.Embed(
        title="🌐 Available languages",
        description="\n".join(lines),
        color=0x3B88C3,
    )
    embed.set_footer(text="Set yours with *set  •  or react with a flag on a message")
    await ctx.reply(embed=embed, mention_author=False)


# ─── Command: auto (owner only, always French) ────────────────
@bot.command(name="auto")
@is_owner()
async def auto(ctx: commands.Context):
    """Toggle auto-translation (to French) in this channel."""
    cid = ctx.channel.id
    if cid in auto_channels:
        auto_channels.discard(cid)
        data["auto_channels"] = list(auto_channels)
        save_data()
        await ctx.reply("🔴 Auto-translation **disabled** in this channel.")
    else:
        auto_channels.add(cid)
        data["auto_channels"] = list(auto_channels)
        save_data()
        await ctx.reply(
            "🟢 Auto-translation **enabled** in this channel.\n"
            "Messages not in French will be translated to French automatically."
        )


# ─── Command: setreminder (owner only) ────────────────────────
@bot.command(name="setreminder", aliases=["reminderchannel"])
@is_owner()
async def setreminder(ctx: commands.Context, channel: discord.TextChannel = None):
    """Set the channel where the periodic reminder is posted.

    Usage:
      • *setreminder            -> use the current channel
      • *setreminder #channel   -> use a specific channel
      • *setreminder off        -> disable reminders
    """
    # Allow "*setreminder off" to disable.
    if channel is None and ctx.message.content.split()[-1].lower() in ("off", "stop", "none"):
        data["reminder_channel"] = None
        save_data()
        await ctx.reply("🔕 Reminders are now **disabled**.")
        return

    target = channel or ctx.channel
    data["reminder_channel"] = target.id
    save_data()
    await ctx.reply(
        f"🔔 Reminders will now be posted in {target.mention} "
        f"every {REMINDER_MINUTES} minutes."
    )


# ─── Commands: allow / unallow / allows (owner only) ──────────
@bot.command(name="allow")
@is_owner()
async def allow(ctx: commands.Context, channel: discord.TextChannel = None):
    """Allow commands to be used in a channel (current one by default)."""
    target = channel or ctx.channel
    if target.id not in data["allowed_channels"]:
        data["allowed_channels"].append(target.id)
        save_data()
    await ctx.reply(
        f"✅ Commands are now allowed in {target.mention}. "
        f"(`{PREFIX}translate` still works everywhere.)"
    )


@bot.command(name="unallow", aliases=["disallow"])
@is_owner()
async def unallow(ctx: commands.Context, channel: discord.TextChannel = None):
    """Stop allowing commands in a channel (current one by default)."""
    target = channel or ctx.channel
    if target.id in data["allowed_channels"]:
        data["allowed_channels"].remove(target.id)
        save_data()
        await ctx.reply(f"✅ Commands are no longer allowed in {target.mention}.")
    else:
        await ctx.reply(f"{target.mention} wasn't in the allowed list.")


@bot.command(name="allows", aliases=["allowlist"])
@is_owner()
async def allows(ctx: commands.Context):
    """List the channels where commands are allowed."""
    chans = data["allowed_channels"]
    if not chans:
        await ctx.reply(
            "No command channels set yet — commands work **everywhere**. "
            f"Use `{PREFIX}allow` to restrict them to specific channels."
        )
    else:
        lines = "\n".join(f"• <#{cid}>" for cid in chans)
        await ctx.reply(
            f"**Command channels:**\n{lines}\n\n"
            f"(`{PREFIX}translate` works in every channel.)"
        )


# ──────────────────────────────────────────────────────────────
#  LEASH (DOG) SYSTEM
# ──────────────────────────────────────────────────────────────

def can_leash(user_id: int) -> bool:
    """The owner and whitelisted users can use the leash."""
    return user_id == OWNER_ID or user_id in data["wl"]


def dog_count(master_id: int) -> int:
    return sum(1 for v in data["leashes"].values() if v["master_id"] == master_id)


# ─── Command: leash / dog ─────────────────────────────────────
@bot.command(name="leash", aliases=["dog"])
async def leash(ctx: commands.Context, member: discord.Member = None):
    """Leash a member (or unleash them if already leashed)."""
    author = ctx.author.id

    if not can_leash(author):
        await ctx.reply("⛔ You don't have permission to use this command.")
        return
    if member is None:
        await ctx.reply(f"Usage: `{PREFIX}leash <@member or id>`")
        return

    key = leash_key(ctx.guild.id, member.id)

    # ── Already leashed -> unleash (toggle) ──
    if key in data["leashes"]:
        info = data["leashes"][key]
        if author != OWNER_ID and info["master_id"] != author:
            await ctx.reply("⛔ This dog belongs to someone else.")
            return
        del data["leashes"][key]
        save_data()
        try:
            await member.edit(nick=info.get("base_name"))
        except discord.Forbidden:
            pass
        await ctx.reply(f"🦴 {member.mention} is no longer leashed.")
        return

    # ── Otherwise leash them ──
    # Limit: a whitelisted user may only have one dog at a time (owner = unlimited)
    if author != OWNER_ID and dog_count(author) >= 1:
        await ctx.reply(
            "⛔ As a whitelisted user, you can only have **one** dog at a time."
        )
        return

    base_name = member.display_name
    new_nick = format_nick(base_name, ctx.author.display_name)
    try:
        await member.edit(nick=new_nick)
    except discord.Forbidden:
        await ctx.reply(
            "❌ I can't rename this person "
            "(their role is above mine, or they are the server owner)."
        )
        return

    data["leashes"][key] = {
        "master_id": author,
        "master_name": ctx.author.display_name,
        "base_name": base_name,
        "nick": new_nick,
    }
    save_data()
    await ctx.reply(f"{DOG_EMOJI} {member.mention} is now leashed.")


# ─── Command: dogs (list your dogs) ───────────────────────────
@bot.command(name="dogs", aliases=["doglist"])
async def dogs(ctx: commands.Context):
    """Show your dogs (all leashes if you are the owner)."""
    author = ctx.author.id
    if not can_leash(author):
        await ctx.reply("⛔ You don't have permission to use this command.")
        return

    if author == OWNER_ID:
        entries = list(data["leashes"].items())
        title = "🐕 All dogs"
    else:
        entries = [(k, v) for k, v in data["leashes"].items() if v["master_id"] == author]
        title = "🐕 Your dogs"

    if not entries:
        await ctx.reply("No leashed members right now.")
        return

    lines = []
    for key, info in entries:
        _, user_id = key.split(":")
        if author == OWNER_ID:
            lines.append(f"• <@{user_id}> — master: <@{info['master_id']}>")
        else:
            lines.append(f"• <@{user_id}>")

    embed = discord.Embed(title=title, description="\n".join(lines), color=0x8B5A2B)
    await ctx.reply(embed=embed, mention_author=False)


# ─── Command: wl (whitelist management, owner only) ───────────
@bot.command(name="wl")
@is_owner()
async def wl(ctx: commands.Context, action: str = None, member: discord.Member = None):
    """Manage the whitelist: *wl add|remove|list [@member]"""
    if action == "add" and member:
        if member.id not in data["wl"]:
            data["wl"].append(member.id)
            save_data()
        await ctx.reply(f"✅ {member.mention} added to the whitelist.")
    elif action in ("remove", "del") and member:
        if member.id in data["wl"]:
            data["wl"].remove(member.id)
            save_data()
        await ctx.reply(f"✅ {member.mention} removed from the whitelist.")
    elif action == "list":
        if not data["wl"]:
            await ctx.reply("The whitelist is empty.")
        else:
            lines = "\n".join(f"• <@{uid}>" for uid in data["wl"])
            await ctx.reply(f"**Whitelist:**\n{lines}")
    else:
        await ctx.reply(f"Usage: `{PREFIX}wl add|remove|list [@member]`")


# ─── Event: enforce nickname (anti-change) ────────────────────
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    key = leash_key(after.guild.id, after.id)
    info = data["leashes"].get(key)
    if not info:
        return
    if after.nick != info["nick"]:
        try:
            await after.edit(nick=info["nick"])
        except discord.Forbidden:
            pass


# ─── Event: re-apply leash when the member rejoins ────────────
@bot.event
async def on_member_join(member: discord.Member):
    key = leash_key(member.guild.id, member.id)
    info = data["leashes"].get(key)
    if not info:
        return
    try:
        await member.edit(nick=info["nick"])
    except discord.Forbidden:
        pass


# ──────────────────────────────────────────────────────────────
#  HELP  (category dropdown, permission-aware)
# ──────────────────────────────────────────────────────────────

LEVELS = {"member": 0, "wl": 1, "owner": 2}


def perm_level(user_id: int) -> str:
    if user_id == OWNER_ID:
        return "owner"
    if user_id in data["wl"]:
        return "wl"
    return "member"


# Each category declares the minimum level needed to see it.
CATEGORIES = {
    "home": {"label": "Home", "emoji": "🏠", "min": "member"},
    "translate": {"label": "Translate", "emoji": "🌍", "min": "member"},
    "leash": {"label": "Leash", "emoji": "🐕", "min": "wl"},
    "config": {"label": "Configuration", "emoji": "⚙️", "min": "owner"},
}


def allowed_categories(level: str) -> list[str]:
    n = LEVELS[level]
    return [c for c, meta in CATEGORIES.items() if LEVELS[meta["min"]] <= n]


def _embed_home(level: str) -> discord.Embed:
    if level == "owner":
        who = "You're an **owner** — you can see every command.\n"
    elif level == "wl":
        who = "You're **whitelisted** — extra commands are unlocked.\n"
    else:
        who = ""
    embed = discord.Embed(
        title="📖 Bot help",
        description=(
            "Translate messages into any language.\n" + who + "Pick a category below."
        ),
        color=0x3B88C3,
    )
    cats = [c for c in allowed_categories(level) if c != "home"]
    embed.add_field(
        name="Categories",
        value="\n".join(f"{CATEGORIES[c]['emoji']} {CATEGORIES[c]['label']}" for c in cats),
        inline=False,
    )
    embed.set_footer(text=f"Prefix: {PREFIX}")
    return embed


def _embed_translate(level: str) -> discord.Embed:
    embed = discord.Embed(
        title="🌍 Translate",
        description="Everything you need to understand any message.",
        color=0x3B88C3,
    )
    embed.add_field(
        name=f"{PREFIX}set",
        value="**Start here.** Opens a menu to choose your language.",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}translate <text>  (or {PREFIX}tr)",
        value=(
            "Translates text into **your** language.\n"
            f"• Reply to a message with `{PREFIX}translate` to translate it."
        ),
        inline=False,
    )
    embed.add_field(
        name="Flag reactions 🇬🇧 🇫🇷 🇹🇷 …",
        value="React with a flag to translate a message into that language.",
        inline=False,
    )
    embed.add_field(name=f"{PREFIX}languages", value="Show all available languages.", inline=False)
    embed.add_field(name=f"{PREFIX}help", value="Open this help menu.", inline=False)
    return embed


def _embed_leash(level: str) -> discord.Embed:
    embed = discord.Embed(
        title="🐕 Leash",
        description="Commands for whitelisted users and the owner.",
        color=0x8B5A2B,
    )
    embed.add_field(
        name=f"{PREFIX}leash <@member>  (or {PREFIX}dog)",
        value="Leash a member, or unleash them if already leashed.",
        inline=False,
    )
    embed.add_field(name=f"{PREFIX}dogs", value="Show your leashed members.", inline=False)
    wl_value = ", ".join(f"<@{uid}>" for uid in data["wl"]) if data["wl"] else "*(empty)*"
    embed.add_field(name="📋 Whitelist", value=wl_value, inline=False)
    if level == "owner":
        embed.add_field(
            name=f"{PREFIX}wl add|remove|list [@member]",
            value="Manage the whitelist (owner only).",
            inline=False,
        )
    return embed


def _embed_config(level: str) -> discord.Embed:
    embed = discord.Embed(
        title="⚙️ Configuration",
        description="Owner-only setup.",
        color=0x5865F2,
    )
    embed.add_field(
        name=f"{PREFIX}auto",
        value="Toggle auto-translation (to French) in a channel.",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}setreminder [#channel | off]",
        value="Choose the channel for the periodic reminder (or disable it).",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}allow / {PREFIX}unallow / {PREFIX}allows",
        value=(
            "Set which channels commands can be used in (keeps the chat clean). "
            f"`{PREFIX}translate` always works everywhere."
        ),
        inline=False,
    )
    return embed


EMBED_BUILDERS = {
    "home": _embed_home,
    "translate": _embed_translate,
    "leash": _embed_leash,
    "config": _embed_config,
}


class HelpSelect(discord.ui.Select):
    def __init__(self, author_id: int, level: str):
        self.author_id = author_id
        self.level = level
        options = [
            discord.SelectOption(
                label=CATEGORIES[c]["label"], value=c, emoji=CATEGORIES[c]["emoji"]
            )
            for c in allowed_categories(level)
        ]
        super().__init__(placeholder="Choose a category…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"⛔ This menu isn't for you. Run `{PREFIX}help` to open your own.",
                ephemeral=True,
            )
            return
        category = self.values[0]
        embed = EMBED_BUILDERS[category](self.level)
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, author_id: int, level: str):
        super().__init__(timeout=180)
        self.add_item(HelpSelect(author_id, level))


# ─── Command: help ────────────────────────────────────────────
@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    """Open the help menu (categories shown depend on your role)."""
    level = perm_level(ctx.author.id)
    embed = _embed_home(level)
    await ctx.reply(embed=embed, view=HelpView(ctx.author.id, level), mention_author=False)


# ─── Auto-translation of messages ─────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Auto-translate if the channel is enabled (and it's not a command)
    if (
        message.channel.id in auto_channels
        and message.content
        and not message.content.startswith(PREFIX)
        and len(message.content) >= 4  # avoid false positives on short messages
    ):
        lang = detect_language(message.content)
        if (lang and lang.lower() != AUTO_TARGET) or looks_turkish(message.content):
            try:
                translation = translate_text(message.content, AUTO_TARGET)
                name, flag = language_info(lang)
                await message.reply(
                    f"🇫🇷 {translation}  *( {flag} {name} )*", mention_author=False
                )
            except Exception as e:
                print(f"Translation error: {e}")

    # Required so commands keep working
    await bot.process_commands(message)


# ─── Translation by reaction (any flag) ───────────────────────
@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot:
        return
    target = FLAG_TO_LANG.get(str(reaction.emoji))
    if not target:
        return
    message = reaction.message
    if not message.content:
        return
    try:
        translation = translate_text(message.content, target)
        _, flag = language_info(target)
        await message.reply(f"{flag} {translation}", mention_author=False)
    except Exception as e:
        print(f"Translation error: {e}")


# ─── Command error handling ───────────────────────────────────
@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, WrongChannel):
        allowed = data.get("allowed_channels", [])
        mentions = ", ".join(f"<#{cid}>" for cid in allowed)
        await ctx.reply(
            f"⛔ You can't use commands in this channel. "
            f"Please head to {mentions}.\n"
            f"💡 Note: `{PREFIX}translate` works everywhere."
        )
    elif isinstance(error, commands.CheckFailure):
        await ctx.reply("⛔ This command is reserved for the bot owner.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # ignore unknown commands
    else:
        raise error


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "❌ No token found. Set the DISCORD_TOKEN environment variable "
            "before running the bot."
        )
    bot.run(TOKEN)
